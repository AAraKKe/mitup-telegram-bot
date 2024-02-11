import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
)
from telegram.ext.filters import BaseFilter

from mitup_bot.handlers.exceptions import (
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)
from mitup_bot.utils.types import CCT


@dataclass
class HandlerWrapper:
    handler: BaseHandler
    bindable: bool
    group: int = 0


class HandlersRegistry:
    """
    The HandlerRegistry provides an API to register handlers that will then be added to an application through the
    `bind` command.

    The HandlersRegistry is intended to be used as a static class with an internal mapping of handlers. Different
    class methods define the API of this class and are sued as decorators around methods that would be used
    as callbacks for different handlers.
    """

    handlers: dict[str, HandlerWrapper] = {}

    @classmethod
    def register_command(
        cls,
        handler_name: str,
        bindable: bool = True,
        group: int = 0,
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
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]],
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

            cls.handlers[handler_name] = HandlerWrapper(
                handler=CommandHandler(
                    command_name,
                    callback=callback,
                    filters=filters,
                    block=block,
                    has_args=has_args,
                ),
                bindable=bindable,
                group=group,
            )
            return callback

        return wrapper

    @classmethod
    def register_message(
        cls,
        handler_name: str,
        filters: BaseFilter,
        bindable: bool = True,
        group: int = 0,
        block: bool = True,
    ) -> Callable[
        [Callable[[Update, CCT], Any]],
        Callable[[Update, CCT], Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator used to register a callback for a MessageHandler.

        Every argument provided is the same as those that can be provided to a MessageHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.messagehandler.html

        Args:
            handler_name (str): Mandatory argument defining the name of the handler to register. This must be unique.
            filters (BaseFilter | None, default = None): The filters to apply to the command as defined in MessageHandler.
                Defaults to None
            block (bool): Whether the command should block other handlers.
                Defaults to False.

        Raises:
            HandlerRegisteredError: If a handler with the same handler_name has already been registered.
        """  # noqa: E501

        def wrapper(
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]],
        ) -> Callable[[Update, CCT], Coroutine[Any, Any, Any]]:
            if handler_name in cls.handlers:
                raise HandlerRegisteredError(callback.__name__)

            cls.handlers[handler_name] = HandlerWrapper(
                handler=MessageHandler(filters=filters, callback=callback, block=block),
                bindable=bindable,
                group=group,
            )
            return callback

        return wrapper

    @classmethod
    def register_callback_query(
        cls,
        handler_name: str,
        bindable: bool = True,
        group: int = 0,
        pattern: str | None = None,
        block: bool = True,
    ) -> Callable[
        [Callable[[Update, CCT], Any]],
        Callable[[Update, CCT], Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator used to register a callback for a CallbackQueryHandler.

        Every argument provided is the same as those that can be provided to a CallbackQueryHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.callbackqueryhandler.html

        Args:

            handler_name (str): Mandatory argument defining the name of the handler to register. This must be unique.
            pattern (str | None): The pattern to register. If the pattern name is not supplied, the method name is
                obtained from the decorated method by following the naming convention: callback_query_<name>.
                Defaults to None.
            block (bool): Whether the command should block other handlers.
                Defaults to False.

        Raises:
            HandlerRegisteredError: If a handler with the same handler_name has already been registered.
        """

        def wrapper(
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]],
        ) -> Callable[[Update, CCT], Coroutine[Any, Any, Any]]:
            func_name = callback.__name__

            if handler_name in cls.handlers:
                raise HandlerRegisteredError(func_name)

            cls.handlers[handler_name] = HandlerWrapper(
                handler=CallbackQueryHandler(
                    pattern=pattern,
                    callback=callback,
                    block=block,
                ),
                bindable=bindable,
                group=group,
            )
            return callback

        return wrapper

    @classmethod
    def bind(cls, app: Application):
        """Bind all registered handlers to a given application"""
        for key, wrapper in cls.handlers.items():
            if wrapper.bindable:
                logging.info(f"Binding {key} handler to application")
                app.add_handler(wrapper.handler)

    @classmethod
    def get_handler(cls, key: str) -> BaseHandler:
        if key not in cls.handlers:
            raise HandlerNotRegistered(key)
        return cls.handlers[key].handler

    @classmethod
    def register_conversation_handler(
        cls,
        handler_name: str,
        entry_points_handler_names: list[str],
        states: dict[Enum, list[str]],
        fallbacks: list[str],
        bindable: bool = True,
        group: int = 0,
        allow_reentry: bool = False,
        per_chat: bool = True,
        per_user: bool = True,
        per_message: bool = False,
        conversation_timeout: float | timedelta | None = None,
        persistent: bool = False,
        map_to_parent: dict[object, object] | None = None,
        block: bool = True,
    ):
        if handler_name in cls.handlers:
            raise HandlerRegisteredError(handler_name)

        missing_handlers = [name for name in entry_points_handler_names if name not in cls.handlers]
        missing_handlers += [name for state in states.values() for name in state if name not in cls.handlers]
        missing_handlers += [name for name in fallbacks if name not in cls.handlers]

        if missing_handlers:
            raise HandlerNotRegistered(", ".join(missing_handlers))

        cls.handlers[handler_name] = HandlerWrapper(
            ConversationHandler(
                entry_points=[cls.handlers[name].handler for name in entry_points_handler_names],
                states={
                    state: [cls.handlers[name].handler for name in state_handlers]
                    for state, state_handlers in states.items()
                },
                fallbacks=[cls.handlers[name].handler for name in fallbacks],
                allow_reentry=allow_reentry,
                per_chat=per_chat,
                per_user=per_user,
                per_message=per_message,
                conversation_timeout=conversation_timeout,
                persistent=persistent,
                map_to_parent=map_to_parent,
                name=handler_name,
                block=block,
            ),
            bindable=bindable,
            group=group,
        )
