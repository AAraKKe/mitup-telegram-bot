import logging
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import Application, BaseHandler, CommandHandler
from telegram.ext.filters import BaseFilter

from mitup_bot.handlers.exceptions import (
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)
from mitup_bot.utils.types import CCT


class HandlersRegistry:
    """
    The HandlerRegistry provides an API to register handlers that will then be added to an application through the
    `bind` command.

    The HandlersRegistry is intended to be used as a static class with an internal mapping of handlers. Different
    class methods define the API of this class and are sued as decorators around methods that would be used
    as callbacks for different handlers.
    """

    handlers: dict[str, BaseHandler] = {}

    @classmethod
    def register_command(
        cls,
        handler_name: str,
        command: str | None = None,
        filters: BaseFilter | None = None,
        block: bool = True,
        has_args: bool | int | None = None,
    ) -> Callable[
        [Callable[[Update, CCT], Any]],
        Callable[[Update, CCT], Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator used to register a callback for a CommandHandler.

        Every argument provided is the same as those that can be provided to a CommandHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.commandhandler.html

        Args:
            handler_name (str): Mandatory argument defining the name of the handler to register. This must be unique.
            command (str | None): The command to register. If the command name is not supplied, the method name is obtained from the decorated method by following the naming convention: command_<name>.
                Defaults to None.
            filters (BaseFilter | None, default = None): The filters to apply to the command as defined in CommandHandler.
                Defaults to None
            block (bool): Whether the command should block other handlers.
                Defaults to False.
            has_args (bool | int | None): Whether the command has arguments. Check CommandHanlder for more informaiton.
                Defaults to None.

        Raises:
            WrongCommandNameError: If the method name does not match the naming convention and no command name
                is provided.
            HandlerRegisteredError: If a handler with the same handler_name has already been registered.
        """  # noqa: E501

        def wrapper(
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]]
        ) -> Callable[[Update, CCT], Coroutine[Any, Any, Any]]:
            func_name = callback.__name__
            if command is None and not func_name.startswith("command_"):
                raise WrongCommandNameError(
                    f"The method name of the method {func_name!r} does not match the naming of a command callback. "
                    "To register a CommandCallback either specify the command or follow the naming convention."
                )

            command_name = command or func_name.replace("command_", "")

            if handler_name in cls.handlers:
                raise HandlerRegisteredError(func_name)

            cls.handlers[handler_name] = CommandHandler(
                command_name,
                callback=callback,
                filters=filters,
                block=block,
                has_args=has_args,
            )
            return callback

        return wrapper

    @classmethod
    def bind(cls, app: Application):
        """Bind all registered handlers to a given application"""
        for key, handler in cls.handlers.items():
            logging.info(f"Binding {key} handler to application")
            app.add_handler(handler)

    @classmethod
    def get_handler(cls, key: str) -> BaseHandler:
        if key not in cls.handlers:
            raise HandlerNotRegistered(key)
        return cls.handlers[key]
