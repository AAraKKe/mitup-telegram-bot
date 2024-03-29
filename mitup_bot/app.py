import logging

from telegram import constants
from telegram.ext import Application, ContextTypes, Defaults, ExtBot

from mitup_bot import db
from mitup_bot.cli.options import Env
from mitup_bot.config import (
    Config,
    EnvVariablesConfigProvider,
    RunModes,
    TomlConfigProvider,
)
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry


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

    def __setup_db(self):
        db.configure_db(self.config.db)

    def __build_application(self) -> Application:
        builder = Application.builder()
        logging.info(HandlersRegistry.handlers)
        builder.token(self.config.bot.token.get_secret_value())

        # Set markdown as default
        builder.defaults(Defaults(parse_mode=constants.ParseMode.MARKDOWN_V2))

        # Set custom context type
        builder.context_types(ContextTypes(context=MitupContext[ExtBot], user_data=MitupUserData))

        app = builder.build()

        HandlersRegistry.bind(app)

        return app

    def run(self):
        if self.config.app.run_mode is RunModes.POLLING:
            self.app.run_polling()
        else:
            self.app.run_webhook()
