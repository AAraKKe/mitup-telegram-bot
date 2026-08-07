from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import assert_never

import structlog
from fastapi import FastAPI, Request, Response
from telegram.ext import Application

from mitup_bot.config import RunModes
from mitup_bot.monitoring.client import MetricsClient, bound_metrics_client
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon import webhooks as patreon_webhooks
from mitup_bot.web import patreon, telegram
from mitup_bot.web.access_log import UnroutedRequests, matched_a_route, unrouted_request_reporting

log = structlog.get_logger(__name__)

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


async def run_shutdown_step(step: Callable[[], Awaitable[None]], metrics_client: MetricsClient, label: str):
    """Run a single shutdown step, isolating its failure so the remaining steps still run."""
    try:
        await step()
    except Exception:
        metrics_client.emit(MetricKey.LIFESPAN_SHUTDOWN_FAILED)
        log.exception("Lifespan shutdown step failed", label=label, reason="shutdown_step_failed")
        return
    # Isolation means a failed step leaves the rest running, so a clean teardown and a partial one
    # are the same silence unless each step that did finish says so.
    log.info("Completed shutdown step", label=label)


def build_webhook_lifespan(
    ptb_app: Application,
    secret_token: str | None,
    metrics_client: MetricsClient,
    webhook_url: str | None,
    max_connections: int | None,
    patreon_webhook_url: str | None,
    unrouted: UnroutedRequests,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("Starting the bot application", run_mode=RunModes.WEBHOOK.value)
        try:
            await ptb_app.initialize()
            await ptb_app.start()
            await ptb_app.bot.set_webhook(
                url=webhook_url,
                secret_token=secret_token,
                max_connections=max_connections,
            )
            # The URL is ours and carries no token (PTB puts the bot token in the URL it calls, not
            # in the one it registers), and it is the setting nobody can otherwise confirm: a
            # webhook pointed at a stale host drops every update with every alarm green.
            log.info(
                "Registered the Telegram webhook",
                webhook_url=webhook_url,
                max_connections=max_connections,
                secret_configured=secret_token is not None,
            )
        except Exception:
            metrics_client.emit(MetricKey.LIFESPAN_STARTUP_FAILED)
            log.exception("Lifespan startup failed in webhook mode")
            # The re-raise aborts the process before any request-cycle flush could run, so the
            # failure metric must be flushed here or it never reaches CloudWatch.
            await metrics_client.flush()
            raise

        # Register the Patreon membership webhook after Telegram's. Unlike set_webhook this is fully
        # failure-isolated (register_membership_webhook swallows its own errors): Patreon is optional
        # and the daily job is the backstop, so a registration failure must not abort startup. Only
        # set when Patreon is configured and a public domain exists (built in app.py).
        if patreon_webhook_url is None:
            log.info("Skipping the Patreon webhook registration", reason="no_patreon_webhook_url")
        else:
            await patreon_webhooks.register_membership_webhook(patreon_webhook_url, metrics_client)
        await metrics_client.flush()
        log.info("Bot application ready", run_mode=RunModes.WEBHOOK.value)

        try:
            async with unrouted_request_reporting(unrouted, metrics_client):
                yield
        finally:
            log.info("Stopping the bot application", run_mode=RunModes.WEBHOOK.value)
            await run_shutdown_step(ptb_app.stop, metrics_client, "ptb_app.stop")
            await run_shutdown_step(ptb_app.shutdown, metrics_client, "ptb_app.shutdown")
            await metrics_client.flush()
            # A clean stop and a process killed mid-teardown are indistinguishable without it.
            log.info("Bot application stopped", run_mode=RunModes.WEBHOOK.value)

    return lifespan


def build_polling_lifespan(
    ptb_app: Application,
    metrics_client: MetricsClient,
    unrouted: UnroutedRequests,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("Starting the bot application", run_mode=RunModes.POLLING.value)
        try:
            await ptb_app.initialize()
            assert ptb_app.updater is not None, "Polling mode must keep the default Updater"
            await ptb_app.updater.start_polling()
            log.info("Started polling for updates")
            await ptb_app.start()
        except Exception:
            metrics_client.emit(MetricKey.LIFESPAN_STARTUP_FAILED)
            log.exception("Lifespan startup failed in polling mode")
            # The re-raise aborts the process before any request-cycle flush could run, so the
            # failure metric must be flushed here or it never reaches CloudWatch.
            await metrics_client.flush()
            raise
        await metrics_client.flush()
        log.info("Bot application ready", run_mode=RunModes.POLLING.value)

        try:
            async with unrouted_request_reporting(unrouted, metrics_client):
                yield
        finally:
            log.info("Stopping the bot application", run_mode=RunModes.POLLING.value)
            assert ptb_app.updater is not None
            await run_shutdown_step(ptb_app.updater.stop, metrics_client, "ptb_app.updater.stop")
            await run_shutdown_step(ptb_app.stop, metrics_client, "ptb_app.stop")
            await run_shutdown_step(ptb_app.shutdown, metrics_client, "ptb_app.shutdown")
            await metrics_client.flush()
            # A clean stop and a process killed mid-teardown are indistinguishable without it.
            log.info("Bot application stopped", run_mode=RunModes.POLLING.value)

    return lifespan


def create_app(
    ptb_app: Application,
    *,
    secret_token: str | None,
    metrics_client: MetricsClient,
    run_mode: RunModes,
    webhook_url: str | None = None,
    max_connections: int | None = None,
    patreon_webhook_url: str | None = None,
) -> FastAPI:
    """Build the FastAPI application that hosts the PTB webhook and side routes.

    The factory owns the lifespan: it initializes/starts PTB on enter and tears
    it down on exit. Routers read the PTB app, secret token, and metrics client
    via FastAPI's DI from ``app.state``.
    """
    unrouted = UnroutedRequests()

    match run_mode:
        case RunModes.WEBHOOK:
            lifespan = build_webhook_lifespan(
                ptb_app, secret_token, metrics_client, webhook_url, max_connections, patreon_webhook_url, unrouted
            )
        case RunModes.POLLING:
            lifespan = build_polling_lifespan(ptb_app, metrics_client, unrouted)
        case _ as unreachable:
            assert_never(unreachable)

    # This app is a public webhook receiver, so the interactive docs and the schema they read stay
    # off: they would publish a machine-readable inventory of every route to anonymous callers.
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ptb_app = ptb_app
    app.state.secret_token = secret_token
    app.state.metrics_client = metrics_client
    app.state.unrouted_requests = unrouted

    app.include_router(telegram.router)
    app.include_router(patreon.router)

    @app.middleware("http")
    async def observe_and_flush_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Endpoint emissions only buffer inside the client; without a per-request flush they
        # never reach CloudWatch and accumulate in memory for the process lifetime. The finally
        # covers error responses and unhandled endpoint exceptions alike.
        #
        # Publishing the client here is what lets an outbound call made deep inside an endpoint —
        # the Patreon round-trips behind the OAuth callback and the membership webhook — land its
        # timing sample in the window this request flushes, instead of finding no client at all.
        #
        # A request no route matched is only counted — the lifespan's reporter publishes the
        # accounting on an interval, so a scan costs one metric sample and one summary line a
        # minute rather than anything per request — and deliberately not flushed: the router
        # answers 404 without reaching an endpoint, so nothing can have emitted, while a flush
        # walks every logger the backend has cached and writes a document for each, putting the
        # per-request cost straight back. Were something below to emit anyway it stays buffered
        # until the next served request, not lost. A request a route did serve is narrated by the
        # endpoint that served it. `url.path` carries no query string, and the accounting must
        # never see one: a near-miss on a real route could carry a live OAuth code in its query.
        try:
            with bound_metrics_client(metrics_client):
                return await call_next(request)
        finally:
            if matched_a_route(request):
                await metrics_client.flush()
            else:
                unrouted.record(request.method, request.url.path)

    return app
