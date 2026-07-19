from typing import TYPE_CHECKING, assert_never

import structlog
import uvicorn
from telegram.ext import AIORateLimiter, Application, ContextTypes

from mitup_bot import api_guards, db, docs_links, hosts_group, patreon, reconcile, supporter, timezone_api
from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, RunModes, TomlConfigProvider
from mitup_bot.custom_context import BOT_CONFIG_KEY, MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.logging_config import Component, configure_logging
from mitup_bot.models import configure_token_encryption
from mitup_bot.monitoring.backend import EmfBackend, configure_emf_backend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import webhooks as patreon_webhooks
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
        # Build config before configuring logging: the log level is sourced from config.
        self.config = Config.from_providers(
            EnvVariablesConfigProvider(),
            TomlConfigProvider(env=env),
        )
        configure_logging(self.env, Component.BOT, self.config.app.log_level)
        # Adopt the merged free-tier limits so the supporter-tier policy resolves caps against the
        # deployed values.
        supporter.configure(self.config.limits)
        # Adopt the hosts-only group settings so the join-request handler and the Patreon web layer
        # can gate on them; the feature stays a no-op until a chat id is configured.
        hosts_group.configure(self.config.bot)
        # Point docs-site links at this environment's docs host, derived from the bot domain.
        docs_links.configure(self.config.bot.domain)
        self.app = self.__build_application()
        # The api rendering layer validates incoming Updates through an injected guards slot;
        # wire the guards-backed validators before any update is processed.
        api_guards.register_update_guards()
        # Metrics before db: the pool-metrics client emits through the process-global EMF
        # configuration, which must be in place before the db layer starts using it.
        self.__configure_metrics()
        self.__setup_db()
        self.__setup_timezone_api()
        self.__setup_patreon()

    def __setup_patreon(self):
        """Wire the token cipher and the Patreon runtime holder from the required config section."""
        configure_token_encryption(*self.config.patreon.encryption_keys())
        patreon.configure(self.config.patreon)

    def __setup_db(self):
        metrics_client = MetricsClient(EmfBackend()) if self.config.db.pool_metrics_enabled else None
        db.configure_db(self.config.db, metrics_client=metrics_client)
        reconcile.register_outbox_reconciler()

    def __setup_timezone_api(self):
        timezone_api.configure(self.config.google_api)

    def __configure_metrics(self):
        configure_emf_backend(self.config.metrics)
        log.info("Metrics configuration set", config=self.config.metrics)

    def __build_application(self) -> Application:
        builder = Application.builder()
        builder.token(self.config.bot.token.get_secret_value())

        # Set custom context type
        builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))

        # Set rate limiter
        builder.rate_limiter(AIORateLimiter(max_retries=self.config.bot.retries_on_throttle))

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

        return app

    def __build_polling_fastapi_app(self, metrics_client: MetricsClient) -> FastAPI:
        return create_app(
            self.app,
            secret_token=None,
            metrics_client=metrics_client,
            run_mode=RunModes.POLLING,
        )

    def __build_webhook_fastapi_app(self, metrics_client: MetricsClient) -> FastAPI:
        if self.config.bot.domain is None:
            raise ValueError("Domain must be set when running with webhook")
        if self.config.bot.secret_token is None:
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
            webhook_url=f"https://{self.config.bot.domain}:{self.config.bot.port}/telegram",
            max_connections=self.config.bot.max_connections,
            patreon_webhook_url=patreon_webhook_url,
        )

    def run(self):
        log.info("Running Mitup", env=self.env)

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
        server = uvicorn.Server(
            uvicorn.Config(
                app=fastapi_app,
                host="0.0.0.0",
                port=self.config.bot.listen_port,
                workers=1,
                log_config=None,
                lifespan="on",
            )
        )
        server.run()
