from enum import Enum
from typing import Any, cast, overload

from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.api_wrapper import ContextOrBotAdapter
from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from tests.helpers.handler_context import HandlerContext

from .api import MockApi
from .monitoring import StubMetrics, StubMetricsEngine
from .types import StubMitupApp, StubMitupContext


def build_context(
    update: Update,
    app: StubMitupApp,
    with_meeting_id: dict[ContextId, int] | None = None,
) -> StubMitupContext:
    if update.effective_message:
        update.effective_message.set_bot(app.bot)

    # Build test metrics engine
    metrics_engine = StubMetricsEngine(logger_provider=lambda _: StubMetrics())

    context = MitupContext.from_update(update=update, application=app)
    context.api = MockApi()
    context.api.adapter = cast(ContextOrBotAdapter, context)
    context.metrics_engine = metrics_engine

    # Allow the engine to access the context
    metrics_engine.parent_context = context

    for context_id, meeting_id in (with_meeting_id or {}).items():
        assert context.user_data is not None
        context.user_data.store_meeting_id(context_id, meeting_id)

    return cast(StubMitupContext, context)


@overload
async def call_handler(
    handler_id: HandlerId,
    *,
    update: Update,
    app: StubMitupApp,
    with_meeting_id: dict[ContextId, int] | None = None,
    user_id: int | None = None,
) -> tuple[StubMitupContext, Enum | None]:
    """
    Call a handler directly with the given update and app, building the context.

    Args:
        handler_id: The ID of the handler to call.
        update: The Update object to process.
        app: The StubMitupApp instance.
        with_meeting_id: Optional mapping of ContextId to meeting IDs to pre-populate in the context.
        user_id: Optional user ID to get the state of the conversation handler, if any.
    """


@overload
async def call_handler(
    handler_id: HandlerId,
    *,
    handler_context: HandlerContext,
    with_meeting_id: dict[ContextId, int] | None = None,
    user_id: int | None = None,
) -> tuple[StubMitupContext, None]:
    """
    Call a handler directly with the given HandlerContext, building the context.

    Args:
        handler_id: The ID of the handler to call.
        handler_context: The HandlerContext containing the update and app.
        with_meeting_id: Optional mapping of ContextId to meeting IDs to pre-populate in the context.
        user_id: Optional user ID to get the state of the conversation handler, if any.
    """


async def call_handler(
    handler_id: HandlerId,
    **kwargs: Any,
) -> tuple[StubMitupContext, Enum | None]:
    update: Update | None = cast(Update | None, kwargs.get("update"))
    app: StubMitupApp | None = cast(StubMitupApp | None, kwargs.get("app"))
    with_meeting_id: dict[ContextId, int] | None = cast(dict[ContextId, int] | None, kwargs.get("with_meeting_id"))
    handler_context: HandlerContext | None = cast(HandlerContext | None, kwargs.get("handler_context"))

    real_update: Update
    real_app: StubMitupApp

    if handler_context is not None:
        real_update = handler_context.update
        real_app = handler_context.app
    elif update is not None and app is not None:
        real_update = update
        real_app = app
    else:
        raise ValueError(
            "Invalid call_handler usage. You must provide either 'handler_context' or "
            "both 'update' and 'app' as keyword arguments."
        )

    context = build_context(real_update, real_app, with_meeting_id)

    handler = HandlersRegistry.get_handler(handler_id)

    # Allow natural handling of the request data provided on the update
    check_result = handler.check_update(real_update)
    assert check_result is not None, "This update would not be processed by the handler!"
    assert check_result is not False, "This update would not be processed by the handler!"

    # Force cast becuase PTB forces a return type when declaring handlers and set `object` as return type
    # of ConversationHandlers which prevents us from using specific types as the return type is invariant
    handler_result = cast(Enum | None, await handler.handle_update(real_update, real_app, check_result, context))

    # For conversation handlers, lets get the current state so we can assert on it
    if isinstance(handler, ConversationHandler):
        user_id = kwargs.get("user_id", real_update.effective_user.id if real_update.effective_user else None)
        handler_result = cast(Enum | None, handler._conversations.get((user_id,)))
    return context, handler_result
