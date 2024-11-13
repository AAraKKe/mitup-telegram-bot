import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from time import perf_counter
from warnings import filterwarnings

from aws_embedded_metrics.unit import Unit
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
)
from telegram.ext.filters import BaseFilter
from telegram.warnings import PTBUserWarning

from mitup_bot import guards
from mitup_bot.callback_data import CallbackData
from mitup_bot.callback_id import CallbackId
from mitup_bot.config import Env
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import HandlerNotRegistered, HandlerRegisteredError, WrongCommandNameError
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.mitup_types import HandlerCallback, TMitupContext

from .error_handler import handler as error_handler

# Remove the warning that is sent when using the per_message option in the registry.
# We have a case in which the user can interact with a simialr message in different palces
# but the handler for that will never be part of a conversation handler. The users only interact
# witht he bot through the bot chat.
# For more information:
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do
filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


def callback_with_metrics(
    callback_id: CallbackId, handler_type: str, callback: HandlerCallback, env: Env
) -> HandlerCallback:
    async def inner_callback(update: Update, context: TMitupContext):
        # Set Handler as dimensions for every metric emission from within a callback
        # Setting them as default dimensions so any flush does not remove them and we also
        # override aws default dimensions we are not interested in.
        context.prepare_handler_metrics({"Handler": callback_id.dimension, "HandlerType": handler_type})
        start = perf_counter()
        return_value = None
        try:
            return_value = await callback(update, context)
        except Exception as e:
            # Relying on error handlers by the application will result in the creation of a
            # separate context. Lets handle errors here where we still have the context
            # of the handler that was executed including metrics context.
            await error_handler(context, e, env)
        else:
            context.put_metric(MetricKey.FAULT, 0)
            # Emit error without dimensions as well
            context.put_custom_metric(MetricKey.FAULT, 0, Unit.COUNT)
        finally:
            latency = (perf_counter() - start) * 1000
            context.put_metric(MetricKey.TIME, latency, Unit.MILLISECONDS)
            # Emit latency without dimensions as well
            context.put_custom_metric(MetricKey.TIME, latency, Unit.MILLISECONDS)

            # Make sure we flush the metrics after every callback to drain any buffered metrics
            await context.flush_metrics()
        return return_value

    return inner_callback


@dataclass
class HandlerWrapper:
    handler: BaseHandler[Update, MitupContext, object]
    bindable: bool
    group: int = 0
    env: Env | None = None

    def is_conversation(self) -> bool:
        return isinstance(self.handler, ConversationHandler)


async def callback_query_fallback(update: Update, context: TMitupContext):
    """Fallback callback query handler. This will be called when no other callback query handler is found."""
    callback_query = guards.callback_query(update)

    # No need to create a message for this as there will be no transaltions. Before translations
    # are added all features should be finished.
    message = "Sorry, I don't understand that yet.\nThis feature will be available soon! Stay tuned! 😄🚀"
    logging.info(update)
    with suppress(TelegramError):
        await context.bot.answer_callback_query(callback_query.id, message, show_alert=True)


class HandlersRegistry:
    """
    The HandlerRegistry provides an API to register handlers that will then be added to an application through the
    `bind` command.

    The HandlersRegistry is intended to be used as a static class with an internal mapping of handlers. Different
    class methods define the API of this class and are sued as decorators around methods that would be used
    as callbacks for different handlers.
    """

    env: Env = Env.DEV
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
                raise HandlerRegisteredError(callback_id)

            cls.handlers[callback_id] = HandlerWrapper(
                handler=CommandHandler(
                    command_name,
                    callback=callback_with_metrics(callback_id, "Command", callback, cls.env),
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
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a MessageHandler.

        Every argument provided is the same as those that can be provided to a MessageHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.messagehandler.html
        """  # noqa: E501

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            if callback_id in cls.handlers:
                raise HandlerRegisteredError(callback_id)

            cls.handlers[callback_id] = HandlerWrapper(
                handler=MessageHandler(
                    filters=filters,
                    callback=callback_with_metrics(callback_id, "Message", callback, cls.env),
                    block=block,
                ),
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
        auto_answer: bool = True,
        group: int = 0,
        callback_data: CallbackData | None = None,
        block: bool = True,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a CallbackQueryHandler. Set auto_answer to False if you want to answer
        the callback query from the callback itself. Otherwise, a dummy answer will be sent to Telegram once the
        callback finishes to make sure the Telegram client knows that the callback has been processed.

        Every argument provided is the same as those that can be provided to a CallbackQueryHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.callbackqueryhandler.html
        """

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            if callback_id in cls.handlers:
                raise HandlerRegisteredError(callback_id)

            async def inner_wrapper(update: Update, context: MitupContext):
                result = await callback(update, context)
                if auto_answer:
                    assert update.callback_query is not None
                    with suppress(TelegramError):
                        await context.bot.answer_callback_query(update.callback_query.id)
                return result

            cls.handlers[callback_id] = HandlerWrapper(
                handler=CallbackQueryHandler(
                    pattern=callback_data.pattern if callback_data else None,
                    callback=callback_with_metrics(callback_id, "Callback", inner_wrapper, cls.env),
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
        # Sort them setting conversation handlers first to be sure that any unexpected answer to a conversation is
        # handled by its fallbacks
        sorted_items = sorted(cls.handlers.items(), key=lambda v: v[1].is_conversation(), reverse=True)
        for key, wrapper in sorted_items:
            if wrapper.bindable:
                logging.info(f"Binding {key} handler to application")
                app.add_handler(handler=wrapper.handler, group=wrapper.group)
        # Add a fallback handler for any update that is not handled by any of the registered handlers
        # the intention is that the user gets a message saying that it is not implemented yet instead of
        # the bot not responding at all.
        app.add_handler(CallbackQueryHandler(callback=callback_query_fallback))

    @classmethod
    def get_handler(cls, key: CallbackId) -> BaseHandler[Update, MitupContext, object]:
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
        allow_reentry: bool = True,
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
            raise HandlerNotRegistered(missing_handlers)

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
                name=callback_id.value,
                block=block,
            ),
            bindable=bindable,
            group=group,
        )

    @classmethod
    def register_inline_handler(
        cls,
        callback_id: CallbackId,
        bindable: bool = True,
        group: int = 0,
        block: bool = True,
        pattern: str | None = None,
        chat_types: list[str] | None = None,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        if callback_id in cls.handlers:
            raise HandlerRegisteredError(callback_id)

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            cls.handlers[callback_id] = HandlerWrapper(
                handler=InlineQueryHandler(
                    callback=callback_with_metrics(callback_id, "InlineQuery", callback, cls.env),
                    pattern=pattern,
                    chat_types=chat_types,
                    block=block,
                ),
                bindable=bindable,
                group=group,
            )

            return callback

        return wrapper
