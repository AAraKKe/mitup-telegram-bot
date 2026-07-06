from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import assert_never

import structlog
from fastapi import FastAPI
from telegram.ext import Application

from mitup_bot.config import RunModes
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon import webhooks as patreon_webhooks
from mitup_bot.web import patreon, telegram

log = structlog.get_logger(__name__)

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


async def run_shutdown_step(step: Callable[[], Awaitable[None]], metrics_client: MetricsClient, label: str):
    """Run a single shutdown step, isolating its failure so the remaining steps still run."""
    try:
        await step()
    except Exception:
        metrics_client.emit(MetricKey.LIFESPAN_SHUTDOWN_FAILED)
        log.exception("Lifespan shutdown step failed", label=label)


def build_webhook_lifespan(
    ptb_app: Application,
    secret_token: str | None,
    metrics_client: MetricsClient,
    webhook_url: str | None,
    max_connections: int | None,
    patreon_webhook_url: str | None,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await ptb_app.initialize()
            await ptb_app.start()
            await ptb_app.bot.set_webhook(
                url=webhook_url,
                secret_token=secret_token,
                max_connections=max_connections,
            )
        except Exception:
            metrics_client.emit(MetricKey.LIFESPAN_STARTUP_FAILED)
            log.exception("Lifespan startup failed in webhook mode")
            raise

        # Register the Patreon membership webhook after Telegram's. Unlike set_webhook this is fully
        # failure-isolated (register_membership_webhook swallows its own errors): Patreon is optional
        # and the daily job is the backstop, so a registration failure must not abort startup. Only
        # set when Patreon is configured and a public domain exists (built in app.py).
        if patreon_webhook_url is not None:
            await patreon_webhooks.register_membership_webhook(patreon_webhook_url, metrics_client)

        try:
            yield
        finally:
            await run_shutdown_step(ptb_app.stop, metrics_client, "ptb_app.stop")
            await run_shutdown_step(ptb_app.shutdown, metrics_client, "ptb_app.shutdown")

    return lifespan


def build_polling_lifespan(
    ptb_app: Application,
    metrics_client: MetricsClient,
) -> Lifespan:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await ptb_app.initialize()
            assert ptb_app.updater is not None, "Polling mode must keep the default Updater"
            await ptb_app.updater.start_polling()
            await ptb_app.start()
        except Exception:
            metrics_client.emit(MetricKey.LIFESPAN_STARTUP_FAILED)
            log.exception("Lifespan startup failed in polling mode")
            raise

        try:
            yield
        finally:
            assert ptb_app.updater is not None
            await run_shutdown_step(ptb_app.updater.stop, metrics_client, "ptb_app.updater.stop")
            await run_shutdown_step(ptb_app.stop, metrics_client, "ptb_app.stop")
            await run_shutdown_step(ptb_app.shutdown, metrics_client, "ptb_app.shutdown")

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
    match run_mode:
        case RunModes.WEBHOOK:
            lifespan = build_webhook_lifespan(
                ptb_app, secret_token, metrics_client, webhook_url, max_connections, patreon_webhook_url
            )
        case RunModes.POLLING:
            lifespan = build_polling_lifespan(ptb_app, metrics_client)
        case _ as unreachable:
            assert_never(unreachable)

    app = FastAPI(lifespan=lifespan)
    app.state.ptb_app = ptb_app
    app.state.secret_token = secret_token
    app.state.metrics_client = metrics_client

    app.include_router(telegram.router)
    app.include_router(patreon.router)

    return app
