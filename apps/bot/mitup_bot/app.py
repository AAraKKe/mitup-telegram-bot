from typing import TYPE_CHECKING, assert_never

import structlog
import uvicorn
from telegram.ext import Application, ContextTypes

from mitup_bot import api_guards, db, docs_links, hosts_group, patreon, reconcile, supporter, timezone_api
from mitup_bot.bootstrap import load_config
from mitup_bot.card_refresh import WorkerLimits
from mitup_bot.config import Env, RunModes
from mitup_bot.custom_context import BOT_CONFIG_KEY, MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.logging_config import Component, configure_logging
from mitup_bot.models import configure_token_encryption
from mitup_bot.monitoring.backend import EmfBackend, configure_emf_backend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import webhooks as patreon_webhooks
from mitup_bot.rate_limiter import MitupRateLimiter
from mitup_bot.request import build_telegram_request
from mitup_bot.update_processor import PerUserUpdateProcessor
from mitup_bot.web import create_app

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

log = structlog.get_logger(__name__)


class MitupRuntime:
    """
    The intention of the runtime class is to serve as an entry point through the CLI with a single
    `start` command that kickstarts the bot.

    Any configuration is handled depending on the CLI options and injected into the runtime.
    """

    def __init__(self, env: Env):
        self.env = env
        HandlersRegistry.env = env
        # Build config before configuring logging: the log level and the release marker are both
        # sourced from config, and `load_config` narrates an invalid one before it raises.
        self.config = load_config(env, Component.BOT)
        configure_logging(self.env, Component.BOT, self.config.app.log_level, self.config.app.release)
        self.__setup_supporter_limits()
        self.__setup_hosts_group()
        self.__setup_docs_links()
        self.app = self.__build_application()
        # The api rendering layer validates incoming Updates through an injected guards slot;
        # wire the guards-backed validators before any update is processed.
        api_guards.register_update_guards()
        # Metrics before db: the pool-metrics client emits through the process-global EMF
        # configuration, which must be in place before the db layer starts using it.
        self.__setup_metrics()
        self.__setup_db()
        self.__setup_timezone_api()
        self.__setup_patreon()

    def __setup_supporter_limits(self):
        """Adopt the merged free-tier limits so the supporter-tier policy resolves caps against the
        deployed values."""
        limits = self.config.limits
        supporter.configure(limits)
        log.info(
            "Configured the supporter limits",
            free_active_meetings=limits.free_active_meetings,
            patron_active_meetings=limits.patron_active_meetings,
            free_scheduling_horizon_days=limits.free_scheduling_horizon_days,
            patron_scheduling_horizon_days=limits.patron_scheduling_horizon_days,
            free_participant_capacity=limits.free_participant_capacity,
        )

    def __setup_hosts_group(self):
        """Adopt the hosts-only group settings so the join-request handler and the Patreon web layer
        can gate on them; the feature stays a no-op until a chat id is configured."""
        hosts_group.configure(self.config.bot)
        if not hosts_group.is_configured():
            log.info("Configured the hosts-only group", enabled=False, reason="no_chat_id_configured")
            return

        log.info(
            "Configured the hosts-only group",
            enabled=True,
            chat_id=hosts_group.chat_id(),
            # The invite URL admits its holder to a members-only group, so its presence is the fact
            # worth recording, not its value.
            invite_url_configured=hosts_group.invite_url() is not None,
        )

    def __setup_docs_links(self):
        """Point docs-site links at this environment's docs host, derived from the bot domain."""
        docs_links.configure(self.config.bot.domain)
        log.info("Configured the docs links", docs_base_url=docs_links.DocsState.base_url)

    def __setup_patreon(self):
        """Wire the token cipher and the Patreon runtime holder from the required config section."""
        config = self.config.patreon
        configure_token_encryption(*config.encryption_keys())
        patreon.configure(config)
        log.info(
            "Configured the Patreon integration",
            campaign_id=config.campaign_id,
            # A wrong redirect_uri sends every OAuth attempt to a dead callback, and only Patreon's
            # side would show it; it is our own public endpoint, safe to name.
            redirect_uri=config.redirect_uri,
            # The count is what an operator sets during a key rotation (`new,old`); the keys
            # themselves decrypt every stored token.
            encryption_keys=len(config.encryption_keys()),
            request_timeout_seconds=config.request_timeout_seconds,
            supporter_min_cents=config.supporter_min_cents,
            patron_min_cents=config.patron_min_cents,
            organizer_min_cents=config.organizer_min_cents,
        )

    def __setup_db(self):
        config = self.config.db
        metrics_client = MetricsClient(EmfBackend()) if config.pool_metrics_enabled else None
        db.configure_db(config, metrics_client=metrics_client)
        reconcile.register_outbox_reconciler()
        log.info(
            "Configured the database",
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout,
            pool_metrics_enabled=config.pool_metrics_enabled,
            engine_echo=config.engine_echo,
        )

    def __setup_timezone_api(self):
        timezone_api.configure(self.config.google_api)

    def __setup_metrics(self):
        config = self.config.metrics
        configure_emf_backend(config)
        log.info("Configured the metrics backend", namespace=config.namespace, environment=config.environment.value)

    def __build_application(self) -> Application:
        builder = Application.builder()
        builder.token(self.config.bot.token.get_secret_value())

        # Set custom context type
        builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))

        # Set rate limiter
        builder.rate_limiter(MitupRateLimiter(max_retries=self.config.bot.retries_on_throttle))

        # Short-timeout client for outbound calls; the long-polling request object is left alone.
        builder.request(build_telegram_request(self.config.bot))

        # Updates sharing a (user, chat) key are serialized by construction; distinct keys may
        # overlap once the cap rises above 1. The default cap of 1 keeps processing observably
        # sequential — raising it via config is the deliberate concurrency flip.
        builder.concurrent_updates(PerUserUpdateProcessor(self.config.bot.concurrent_updates))

        # In webhook mode, FastAPI feeds updates directly into Application.process_update so
        # the built-in Updater is unused. Polling mode keeps the default Updater (we drive it
        # manually from the FastAPI lifespan via Updater.start_polling()).
        if self.config.app.run_mode is RunModes.WEBHOOK:
            builder.updater(None)

        app = builder.build()

        # Stash BotConfig so handlers (the broadcast admin gate) can read it via context.bot_data.
        app.bot_data[BOT_CONFIG_KEY] = self.config.bot

        HandlersRegistry.bind(app)

        log.info(
            "Built the bot application",
            run_mode=self.config.app.run_mode.value,
            concurrent_updates=self.config.bot.concurrent_updates,
            retries_on_throttle=self.config.bot.retries_on_throttle,
            api_call_log_enabled=self.config.bot.api_call_log_enabled,
        )

        return app

    def __build_polling_fastapi_app(self, metrics_client: MetricsClient) -> FastAPI:
        return create_app(
            self.app,
            secret_token=None,
            metrics_client=metrics_client,
            run_mode=RunModes.POLLING,
            worker_limits=WorkerLimits.from_config(self.config.app),
            accepts_fanout=self.config.bot.deferred_card_refresh,
        )

    def __build_webhook_fastapi_app(self, metrics_client: MetricsClient) -> FastAPI:
        # Both settings are optional on BotConfig because polling mode needs neither, so webhook
        # mode is where their absence becomes fatal. The line names the missing setting: the bare
        # traceback that follows says only that startup died.
        if self.config.bot.domain is None:
            log.error("Refusing to start in webhook mode", reason="domain_not_set", setting="bot.domain")
            raise ValueError("Domain must be set when running with webhook")
        if self.config.bot.secret_token is None:
            log.error("Refusing to start in webhook mode", reason="secret_token_not_set", setting="bot.secret_token")
            raise ValueError("Secret token must be set when running with webhook")

        # In webhook mode a public domain is guaranteed, so the Patreon membership webhook is
        # registered too. Startup itself stays isolated from any registration failure (handled
        # inside the lifespan); here we only build the URL to attempt.
        patreon_webhook_url = patreon_webhooks.webhook_uri(self.config.bot.domain, self.config.bot.port)

        return create_app(
            self.app,
            secret_token=self.config.bot.secret_token.get_secret_value(),
            metrics_client=metrics_client,
            run_mode=RunModes.WEBHOOK,
            worker_limits=WorkerLimits.from_config(self.config.app),
            accepts_fanout=self.config.bot.deferred_card_refresh,
            webhook_url=f"https://{self.config.bot.domain}:{self.config.bot.port}/telegram",
            max_connections=self.config.bot.max_connections,
            patreon_webhook_url=patreon_webhook_url,
        )

    def run(self):
        log.info(
            "Running Mitup",
            env=self.env.value,
            run_mode=self.config.app.run_mode.value,
            listen_port=self.config.bot.listen_port,
            domain=self.config.bot.domain,
        )

        metrics_client = MetricsClient(EmfBackend())

        match self.config.app.run_mode:
            case RunModes.POLLING:
                fastapi_app = self.__build_polling_fastapi_app(metrics_client)
            case RunModes.WEBHOOK:
                fastapi_app = self.__build_webhook_fastapi_app(metrics_client)
            case _ as unreachable:
                assert_never(unreachable)

        # workers=1: PTB Application owns in-memory state (user_data, conversation states) and
        # is not safe to run across multiple worker processes.
        #
        # access_log=False detaches the `uvicorn.access` logger from the root handler, so the
        # protocol's `hasHandlers()` check is false and it never builds a record for a request.
        # Endpoints narrate the requests they serve, and `web.access_log` counts the ones no route
        # matched, so a scanner sweep costs one metric sample a minute rather than a line each.
        # `uvicorn.error` keeps the root handler: real server errors must still reach the stream.
        server = uvicorn.Server(
            uvicorn.Config(
                app=fastapi_app,
                host="0.0.0.0",
                port=self.config.bot.listen_port,
                workers=1,
                log_config=None,
                access_log=False,
                lifespan="on",
            )
        )
        server.run()
