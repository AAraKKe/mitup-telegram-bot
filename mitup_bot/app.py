import logging
from typing import TYPE_CHECKING, assert_never

import click
import sqlalchemy
import telegram
import telegram.ext
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from telegram.ext import AIORateLimiter, Application, ContextTypes

from mitup_bot import db, timezone_api
from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, RunModes, TomlConfigProvider
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.monitoring.backend import EmfBackend, configure_emf_backend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.web import create_app

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI


class MitupRuntime:
    """
    The intention of the runtime class is to serve as an entry point through the CLI with a single
    `start` command that kickstarts the bot.

    Any configuration is handled depending on the CLI options and injected into the runtime.
    """

    def __init__(self, env: Env):
        self.env = env
        self.__configure_logging()
        HandlersRegistry.env = env
        self.config = Config.from_providers(
            EnvVariablesConfigProvider(),
            TomlConfigProvider(env=env),
        )
        self.app = self.__build_application()
        self.__setup_db()
        self.__setup_timezone_api()
        self.__configure_metrics()

    def __configure_logging(self):
        # Configure logging with RichHandler for better output when debugging locally
        handlers = (
            [
                RichHandler(
                    level=logging.DEBUG,
                    rich_tracebacks=True,
                    tracebacks_suppress=[telegram.ext, telegram, sqlalchemy, click],
                    console=Console(soft_wrap=True, force_terminal=True, width=250),
                )
            ]
            if self.env is Env.DEV
            else None
        )

        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, handlers=handlers
        )

        # Remove https logs
        logging.getLogger("httpx").setLevel(logging.WARNING)

        # Ensure debug logs from bot when working in dev environment
        logging.getLogger("telegram.ext.ExtBot").setLevel(logging.DEBUG if self.env is Env.DEV else logging.WARNING)

    def __setup_db(self):
        db.configure_db(self.config.db)

    def __setup_timezone_api(self):
        timezone_api.configure(self.config.google_api)

    def __configure_metrics(self):
        configure_emf_backend(self.config.metrics)
        logging.info(f"Metrics Configuration set: {self.config.metrics}")

    def __build_application(self) -> Application:
        builder = Application.builder()
        builder.token(self.config.bot.token.get_secret_value())

        # Set custom context type
        builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))

        # Set rate limiter
        builder.rate_limiter(AIORateLimiter(max_retries=self.config.bot.retries_on_throttle))

        # In webhook mode, FastAPI feeds updates directly into Application.process_update so
        # the built-in Updater is unused. Polling mode keeps the default Updater (we drive it
        # manually from the FastAPI lifespan via Updater.start_polling()).
        if self.config.app.run_mode is RunModes.WEBHOOK:
            builder.updater(None)

        app = builder.build()

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

        return create_app(
            self.app,
            secret_token=self.config.bot.secret_token.get_secret_value(),
            metrics_client=metrics_client,
            run_mode=RunModes.WEBHOOK,
            webhook_url=f"https://{self.config.bot.domain}:{self.config.bot.port}/telegram",
            max_connections=self.config.bot.max_connections,
        )

    def run(self):
        logging.info(f"Running Mitup for environment: {self.env}")

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
