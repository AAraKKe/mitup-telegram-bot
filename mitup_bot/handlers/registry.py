from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from time import perf_counter
from warnings import filterwarnings

import structlog
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BaseHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
)
from telegram.ext.filters import BaseFilter
from telegram.warnings import PTBUserWarning

from mitup_bot import db, guards
from mitup_bot.callback_data import CallbackData
from mitup_bot.config import Env
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import HandlerNotRegistered, HandlerRegisteredError, WrongCommandNameError
from mitup_bot.handler_id import HandlerId
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.utils.mitup_types import HandlerCallback, TMitupContext

from .error_handler import handler as error_handler

# Remove the warning that is sent when using the per_message option in the registry.
# We have a case in which the user can interact with a simialr message in different palces
# but the handler for that will never be part of a conversation handler. The users only interact
# witht he bot through the bot chat.
# For more information:
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do
filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

log = structlog.get_logger(__name__)


def callback_with_metrics(
    handler_id: HandlerId, handler_type: str, callback: HandlerCallback, env: Env
) -> HandlerCallback:
    async def inner_callback(update: Update, context: TMitupContext):
        # Attach the handler identity to every metric emitted from within this callback as EMF
        # properties (not dimensions): per-handler drill-down stays available in Logs Insights
        # without minting a billed CloudWatch series per handler.
        context.prepare_handler_metrics({"Handler": handler_id.dimension, "HandlerType": handler_type})

        # Keep the context for counter as Bot to differentiate this from the recurrent events
        db.set_connection_context("Bot")

        # Bind request fields for the duration of this handler. bound_contextvars auto-clears on
        # exit so fields never leak into the next update handled by PTB's reused worker task.
        # merge_contextvars then injects them into every log line emitted downstream — including
        # the metrics and error-handler logs below, which is why they run inside this block.
        with structlog.contextvars.bound_contextvars(**handler_log_context(handler_id, handler_type, update)):
            start = perf_counter()
            return_value = None
            try:
                return_value = await callback(update, context)
            except ApplicationHandlerStop:
                # Not a fault: the handler is claiming the update (it carries the next
                # conversation state). Re-raise so PTB stops the remaining handler groups.
                context.emit_metric(MetricKey.FAULT, 0)
                raise
            except Exception as e:
                # Relying on error handlers by the application will result in the creation of a
                # separate context. Lets handle errors here where we still have the context
                # of the handler that was executed including metrics context.
                await error_handler(context, e, env)
            else:
                # Dimensionless FAULT=0; the handler identity rides as an EMF property, not a dimension.
                context.emit_metric(MetricKey.FAULT, 0)
            finally:
                latency = (perf_counter() - start) * 1000
                # Dimensionless latency; the handler identity rides as an EMF property, not a dimension.
                context.emit_metric(MetricKey.TIME, latency, MetricUnit.MILLISECONDS)

                # Emit leaked database connections (should be 0 if all connections were properly closed)
                context.emit_metric(MetricKey.DB_CONNECTIONS_LEAKED, db.get_open_connections("Bot"), MetricUnit.COUNT)

                # Make sure we flush the metrics after every callback to drain any buffered metrics
                await context.flush_metrics()
            return return_value

    return inner_callback


def guard_admin_only(handler_id: HandlerId, callback: HandlerCallback) -> HandlerCallback:
    """Wrap `callback` so it only runs when the effective user is a bot admin.

    PTB filters cannot express this uniformly: `CallbackQueryHandler` accepts no filters, and the
    check needs the admin allowlist that lives on `context` (via `context.bot_config`), which
    filters never see — so the gate runs here, as part of the measured callback. When a non-admin
    reaches a gated handler the update is dropped and the wrapper returns `None`, which is safe
    inside a `ConversationHandler` (None never changes or creates conversation state). Because the
    admin UI is invisible to non-admins, any drop is a forged or stale interaction, logged as a
    warning with the offending Telegram user id.
    """

    async def inner(update: Update, context: TMitupContext):
        if not guards.is_admin(update, context):
            tg_user_id = update.effective_user.id if update.effective_user else None
            log.warning(
                "Dropped update for admin-only handler from non-admin user",
                handler=handler_id,
                tg_user_id=tg_user_id,
            )
            return None
        return await callback(update, context)

    return inner


def handler_log_context(handler_id: HandlerId, handler_type: str, update: Update) -> dict[str, object]:
    """Build the contextvar fields to bind for a handler call, omitting any the update lacks
    (some updates carry no effective_user/chat)."""
    fields: dict[str, object] = {
        "flow": handler_id.flow,
        "handler": handler_id.dimension,
        "handler_type": handler_type,
        "update_id": update.update_id,
    }
    if update.effective_user is not None:
        fields["tg_user_id"] = update.effective_user.id
    if update.effective_chat is not None:
        fields["chat_id"] = update.effective_chat.id
    return fields


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
    handlers: dict[HandlerId, HandlerWrapper] = {}

    @classmethod
    def register_command(
        cls,
        handler_id: HandlerId,
        bindable: bool = True,
        group: int = 0,
        command: str | None = None,
        filters: BaseFilter | None = None,
        block: bool = True,
        has_args: bool | int | None = None,
        admin_only: bool = False,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a CommandHandler.

        Every argument provided is the same as those that can be provided to a CommandHandler

        Set ``admin_only`` to drop updates from non-admins before the callback runs (see
        ``guard_admin_only``).

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

            if handler_id in cls.handlers:
                raise HandlerRegisteredError(handler_id)

            guarded = guard_admin_only(handler_id, callback) if admin_only else callback

            cls.handlers[handler_id] = HandlerWrapper(
                handler=CommandHandler(
                    command_name,
                    callback=callback_with_metrics(handler_id, "Command", guarded, cls.env),
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
        handler_id: HandlerId,
        filters: BaseFilter,
        bindable: bool = True,
        group: int = 0,
        block: bool = True,
        admin_only: bool = False,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a MessageHandler.

        Every argument provided is the same as those that can be provided to a MessageHandler

        Set ``admin_only`` to drop updates from non-admins before the callback runs (see
        ``guard_admin_only``).

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.messagehandler.html
        """  # noqa: E501

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            if handler_id in cls.handlers:
                raise HandlerRegisteredError(handler_id)

            guarded = guard_admin_only(handler_id, callback) if admin_only else callback

            cls.handlers[handler_id] = HandlerWrapper(
                handler=MessageHandler(
                    filters=filters,
                    callback=callback_with_metrics(handler_id, "Message", guarded, cls.env),
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
        handler_id: HandlerId,
        bindable: bool = True,
        auto_answer: bool = True,
        group: int = 0,
        callback_data: CallbackData | None = None,
        block: bool = True,
        admin_only: bool = False,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a CallbackQueryHandler. Set auto_answer to False if you want to answer
        the callback query from the callback itself. Otherwise, a dummy answer will be sent to Telegram once the
        callback finishes to make sure the Telegram client knows that the callback has been processed.

        Set ``admin_only`` to drop callback queries from non-admins before the callback runs (see
        ``guard_admin_only``). The gate sits inside the auto-answer wrapper, so a dropped query is
        still answered and the client spinner clears.

        Every argument provided is the same as those that can be provided to a CallbackQueryHandler

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.callbackqueryhandler.html
        """

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            if handler_id in cls.handlers:
                raise HandlerRegisteredError(handler_id)

            guarded = guard_admin_only(handler_id, callback) if admin_only else callback

            async def inner_wrapper(update: Update, context: TMitupContext):
                result = await guarded(update, context)
                if auto_answer:
                    assert update.callback_query is not None
                    with suppress(TelegramError):
                        await context.bot.answer_callback_query(update.callback_query.id)
                return result

            cls.handlers[handler_id] = HandlerWrapper(
                handler=CallbackQueryHandler(
                    pattern=callback_data.pattern if callback_data else None,
                    callback=callback_with_metrics(handler_id, "Callback", inner_wrapper, cls.env),
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
        # Sort them, setting conversation handlers first to be sure that any unexpected answer to a conversation is
        # handled by its fallbacks
        sorted_items = sorted(cls.handlers.items(), key=lambda v: v[1].is_conversation(), reverse=True)
        for key, wrapper in sorted_items:
            if wrapper.bindable:
                log.info("Binding handler to application", handler=key)
                app.add_handler(handler=wrapper.handler, group=wrapper.group)
        # Add a fallback handler for any update that is not handled by any of the registered handlers
        # the intention is that the user gets a message saying that it is not implemented yet instead of
        # the bot not responding at all.
        app.add_handler(CallbackQueryHandler(callback=callback_query_fallback))

    @classmethod
    def get_handler(cls, key: HandlerId) -> BaseHandler[Update, MitupContext, object]:
        if key not in cls.handlers:
            raise HandlerNotRegistered(key)
        return cls.handlers[key].handler

    @classmethod
    def register_conversation_handler(
        cls,
        handler_id: HandlerId,
        entry_points_handler_names: list[HandlerId],
        states: dict[Enum, list[HandlerId]],
        fallbacks: list[HandlerId],
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
        if handler_id in cls.handlers:
            raise HandlerRegisteredError(handler_id)

        missing_handlers = [name for name in entry_points_handler_names if name not in cls.handlers]
        missing_handlers += [name for state in states.values() for name in state if name not in cls.handlers]
        missing_handlers += [name for name in fallbacks if name not in cls.handlers]

        if missing_handlers:
            raise HandlerNotRegistered(missing_handlers)

        cls.handlers[handler_id] = HandlerWrapper(
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
                name=handler_id.value,
                block=block,
            ),
            bindable=bindable,
            group=group,
        )

    @classmethod
    def register_chat_member(
        cls,
        handler_id: HandlerId,
        bindable: bool = True,
        group: int = 0,
        block: bool = True,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        """
        Decorator used to register a callback for a ChatMemberHandler restricted to ``my_chat_member`` updates.

        ``my_chat_member`` updates arrive whenever the bot's own membership status changes, e.g. when a user
        blocks or unblocks the bot in a private chat.

        For more information check: https://python-telegram-bot.readthedocs.io/en/stable/telegram.ext.chatmemberhandler.html
        """  # noqa: E501

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            if handler_id in cls.handlers:
                raise HandlerRegisteredError(handler_id)

            cls.handlers[handler_id] = HandlerWrapper(
                handler=ChatMemberHandler(
                    callback=callback_with_metrics(handler_id, "ChatMember", callback, cls.env),
                    chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER,
                    block=block,
                ),
                bindable=bindable,
                group=group,
            )

            return callback

        return wrapper

    @classmethod
    def register_inline_handler(
        cls,
        handler_id: HandlerId,
        bindable: bool = True,
        group: int = 0,
        block: bool = True,
        pattern: str | None = None,
        chat_types: list[str] | None = None,
    ) -> Callable[[HandlerCallback], HandlerCallback]:
        if handler_id in cls.handlers:
            raise HandlerRegisteredError(handler_id)

        def wrapper(
            callback: HandlerCallback,
        ) -> HandlerCallback:
            cls.handlers[handler_id] = HandlerWrapper(
                handler=InlineQueryHandler(
                    callback=callback_with_metrics(handler_id, "InlineQuery", callback, cls.env),
                    pattern=pattern,
                    chat_types=chat_types,
                    block=block,
                ),
                bindable=bindable,
                group=group,
            )

            return callback

        return wrapper
