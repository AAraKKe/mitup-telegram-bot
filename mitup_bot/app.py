import logging

import click
import sqlalchemy
import telegram
import telegram.ext
from rich.console import Console
from rich.logging import RichHandler
from telegram import constants
from telegram.ext import AIORateLimiter, Application, ContextTypes, Defaults

from mitup_bot import db, timezone_api
from mitup_bot.cli.options import Env
from mitup_bot.config import Config, EnvVariablesConfigProvider, RunModes, TomlConfigProvider
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.monitoring import configure_metrics


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

    def __setup_db(self):
        db.configure_db(self.config.db)

    def __setup_timezone_api(self):
        timezone_api.configure(self.config.google_api)

    def __configure_metrics(self):
        configure_metrics(self.config.metrics)

    def __build_application(self) -> Application:
        builder = Application.builder()
        builder.token(self.config.bot.token.get_secret_value())

        # Set markdown as default
        builder.defaults(Defaults(parse_mode=constants.ParseMode.MARKDOWN_V2))

        # Set custom context type
        builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))

        # Set rate limiter
        builder.rate_limiter(AIORateLimiter(max_retries=self.config.bot.retries_on_throttle))

        app = builder.build()

        HandlersRegistry.bind(app)

        return app

    def run(self):
        logging.info(f"Running Mitup for environment: {self.env}")
        if self.config.app.run_mode is RunModes.POLLING:
            self.app.run_polling()
        else:
            if self.config.bot.domain is None:
                raise ValueError("Domain must be set when running with webhook")
            if self.config.bot.secret_token is None:
                raise ValueError("Secret token must be set when running with webhook")

            self.app.run_webhook(
                listen="0.0.0.0",  # This is the address to listen to in the docker container
                secret_token=self.config.bot.secret_token.get_secret_value(),
                webhook_url=f"https://{self.config.bot.domain}:{self.config.bot.port}",
                max_connections=self.config.bot.max_connections,
            )
