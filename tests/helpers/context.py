from enum import Enum
from typing import cast

from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry

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
    context.api.adapter = context
    context.metrics_engine = metrics_engine

    # Allow the engine to access the context
    metrics_engine.parent_context = context

    for context_id, meeting_id in (with_meeting_id or {}).items():
        assert context.user_data is not None
        context.user_data.store_meeting_id(context_id, meeting_id)

    return cast(StubMitupContext, context)


async def call_handler(
    update: Update,
    app: StubMitupApp,
    handler_id: HandlerId,
    with_meeting_id: dict[ContextId, int] | None = None,
) -> tuple[StubMitupContext, Enum | None]:
    context = build_context(update, app, with_meeting_id)

    handler = HandlersRegistry.get_handler(handler_id)

    # Allow natural handling of the request data provided on the update
    check_result = handler.check_update(update)
    assert check_result is not None, "This update would not be processed by the handler!"
    assert check_result is not False, "This update would not be processed by the handler!"

    # Force cast becuase PTB forces a return type when declaring handlers and set `object` as return type
    # of ConversationHandlers which prevents us from using specific types as the return type is invariant
    handler_result = cast(Enum | None, await handler.handle_update(update, app, check_result, context))
    return context, handler_result
