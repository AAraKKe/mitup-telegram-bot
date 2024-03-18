import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, StrEnum
from typing import Any
from warnings import filterwarnings

from telegram import Update
from telegram.ext import (
    Application,
    BaseHandler,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
)
from telegram.ext.filters import BaseFilter
from telegram.warnings import PTBUserWarning

from mitup_bot import guards
from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import HandlerNotRegistered, HandlerRegisteredError, WrongCommandNameError
from mitup_bot.utils.types import CCT, HandlerCallback

# Remove the warning that is sent when using the per_message option in the registry.
# We have a case in which the user can interact with a simialr message in different palces
# but the handler for that will never be part of a conversation handler. The users only interact
# witht he bot through the bot chat.
# For more information:
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do
filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


class CallbackId(StrEnum):
    pass


@dataclass
class HandlerWrapper:
    handler: BaseHandler
    bindable: bool
    group: int = 0


async def callback_query_fallback(update: Update, context: CallbackContext):
    """Fallback callback query handler. This will be called when no other callback query handler is found."""
    callback_query = guards.callback_query(update)

    # No need to create a message for this as there will be no transaltions. Before translations
    # are added all features should be finished.
    message = "Sorry, I don't understand that yet.\nThis feature will be available soon! Stay tuned! 😄🚀"
    await context.bot.answer_callback_query(callback_query.id, message, show_alert=True)


class HandlersRegistry:
    """
    The HandlerRegistry provides an API to register handlers that will then be added to an application through the
    `bind` command.

    The HandlersRegistry is intended to be used as a static class with an internal mapping of handlers. Different
    class methods define the API of this class and are sued as decorators around methods that would be used
    as callbacks for different handlers.
    """

    handlers: dict[CallbackId, HandlerWrapper] = {}

    @classmethod
    def register_command(
        cls,
        callback_id: CallbackId,
        bindable: bool = True,
        group: int = 0,
        command: str | None = None,
        filters: BaseFilter | None = None,
        block: bool = True,
        has_args: bool | int | None = None,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a CommandHandler.

        Every argument provided is the same as those that can be provided to a CommandHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.commandhandler.html
        """  # noqa: E501

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            func_name = callback.__name__
            if command is None and not func_name.startswith("command_"):
                raise WrongCommandNameError(
                    f"The method name of the method {func_name!r} does not match the naming of a command callback. "
                    "To register a CommandCallback either specify the command or follow the naming convention."
                )

            command_name = command or func_name.replace("command_", "")

            if callback_id in cls.handlers:
                raise HandlerRegisteredError(callback_id.value)

            cls.handlers[callback_id] = HandlerWrapper(
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
        callback_id: CallbackId,
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
        """  # noqa: E501

        def wrapper(
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]],
        ) -> Callable[[Update, CCT], Coroutine[Any, Any, Any]]:
            if callback_id in cls.handlers:
                raise HandlerRegisteredError(callback.__name__)

            cls.handlers[callback_id] = HandlerWrapper(
                handler=MessageHandler(filters=filters, callback=callback, block=block),
                bindable=bindable,
                group=group,
            )
            return callback

        return wrapper

    @classmethod
    def register_callback_query(
        cls,
        callback_id: CallbackId,
        bindable: bool = True,
        group: int = 0,
        callback_data: CallbackData | None = None,
        block: bool = True,
    ) -> Callable[
        [Callable[[Update, CCT], Any]],
        Callable[[Update, CCT], Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator used to register a callback for a CallbackQueryHandler.

        Every argument provided is the same as those that can be provided to a CallbackQueryHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.callbackqueryhandler.html
        """

        def wrapper(
            callback: Callable[[Update, CCT], Coroutine[Any, Any, Any]],
        ) -> Callable[[Update, CCT], Coroutine[Any, Any, Any]]:
            func_name = callback.__name__

            if callback_id in cls.handlers:
                raise HandlerRegisteredError(func_name)

            cls.handlers[callback_id] = HandlerWrapper(
                handler=CallbackQueryHandler(
                    pattern=callback_data.pattern if callback_data else None,
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
        # Add a fallback handler for any update that is not handled by any of the registered handlers
        # the intention is that the user gets a message saying that it is not implemented yet instead of
        # the bot not responding at all.
        app.add_handler(CallbackQueryHandler(callback=callback_query_fallback))

    @classmethod
    def get_handler(cls, key: CallbackId) -> BaseHandler:
        if key not in cls.handlers:
            raise HandlerNotRegistered(key)
        return cls.handlers[key].handler

    @classmethod
    def register_conversation_handler(
        cls,
        callback_id: CallbackId,
        entry_points_handler_names: list[CallbackId],
        states: dict[Enum, list[CallbackId]],
        fallbacks: list[CallbackId],
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
        if callback_id in cls.handlers:
            raise HandlerRegisteredError(callback_id)

        missing_handlers = [name for name in entry_points_handler_names if name not in cls.handlers]
        missing_handlers += [name for state in states.values() for name in state if name not in cls.handlers]
        missing_handlers += [name for name in fallbacks if name not in cls.handlers]

        if missing_handlers:
            raise HandlerNotRegistered(", ".join(missing_handlers))

        cls.handlers[callback_id] = HandlerWrapper(
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
                name=callback_id,
                block=block,
            ),
            bindable=bindable,
            group=group,
        )
