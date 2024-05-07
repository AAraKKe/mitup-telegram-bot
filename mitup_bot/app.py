import logging

from telegram import constants
from telegram.ext import Application, ContextTypes, Defaults

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
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO,
        )
        self.config = Config.from_providers(
            EnvVariablesConfigProvider(),
            TomlConfigProvider(env=env),
        )
        self.app = self.__build_application()
        self.__setup_db()
        self.__setup_timezone_api()
        self.__configure_metrics()

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

        app = builder.build()

        HandlersRegistry.bind(app)

        return app

    def run(self):
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
