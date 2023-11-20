import logging

from telegram.ext import Application, BaseHandler, CommandHandler
from telegram.ext._utils.types import HandlerCallback

from mitup_bot.exceptions import (
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)


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
    def register_command(cls, func: HandlerCallback) -> HandlerCallback:
        """
        Decorator to register a CommandHandler. The name of the callback should be `command_something`.
        Decorating a method with such a name results in registering a CommandHandler for the command
        `something`.
        """
        func_name = func.__name__
        if not func_name.startswith("command_"):
            raise WrongCommandNameError(
                f"The method name of the method {func_name!r} does not match the naming of a command callback"
            )

        command_name = func_name.replace("command_", "")

        if func_name in cls.handlers:
            raise HandlerRegisteredError(func_name)
        cls.handlers[func_name] = CommandHandler(command_name, func)

        return func

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
