from enum import Enum
from typing import cast

from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.monitoring import MetricsClient
from tests.helpers.handler_context import HandlerContext
from tests.helpers.monitoring import make_test_metrics_client

from .api import MockApi
from .types import StubMitupApp, StubMitupContext


def build_context(
    update: Update,
    app: StubMitupApp,
    with_meeting_id: dict[ContextId, int] | None = None,
    metrics: MetricsClient | None = None,
) -> StubMitupContext:
    if update.effective_message:
        update.effective_message.set_bot(app.bot)

    client = metrics or make_test_metrics_client()

    context = MitupContext.from_update(update=update, application=app)
    context.api = MockApi()
    context.metrics = client

    for context_id, meeting_id in (with_meeting_id or {}).items():
        assert context.user_data is not None
        context.user_data.store_meeting_id(context_id, meeting_id)

    return cast(StubMitupContext, context)


async def call_handler(
    handler_id: HandlerId,
    *,
    handler_context: HandlerContext,
    with_meeting_id: dict[ContextId, int] | None = None,
    user_id: int | None = None,
) -> tuple[StubMitupContext, Enum | None]:
    """Call a handler directly with the given HandlerContext, building the context.

    Args:
        handler_id: The ID of the handler to call.
        handler_context: The HandlerContext containing the update and app.
        with_meeting_id: Optional mapping of ContextId to meeting IDs to pre-populate in the context.
        user_id: Optional user ID to get the state of the conversation handler, if any.
    """
    update = handler_context.update
    app = handler_context.app

    context = build_context(update, app, with_meeting_id, metrics=handler_context.metrics_client)

    handler = HandlersRegistry.get_handler(handler_id)

    # Allow natural handling of the request data provided on the update
    check_result = handler.check_update(update)
    assert check_result is not None, "This update would not be processed by the handler!"
    assert check_result is not False, "This update would not be processed by the handler!"

    # Force cast becuase PTB forces a return type when declaring handlers and set `object` as return type
    # of ConversationHandlers which prevents us from using specific types as the return type is invariant
    handler_result = cast(Enum | None, await handler.handle_update(update, app, check_result, context))

    # For conversation handlers, retrieve the current state so callers can assert on it.
    #
    # PTB key format contract
    # -----------------------
    # PTB stores conversation state in ConversationHandler._conversations keyed by a
    # tuple that depends on the handler's per_* settings:
    #
    #   per_chat=True,  per_user=True  (default) → key = (chat_id, user_id)
    #   per_chat=False, per_user=True             → key = (user_id,)
    #   per_chat=True,  per_user=False            → key = (chat_id,)
    #   per_chat=False, per_user=False            → key = ()
    #
    # The old code always used (user_id,), which only matched conversations registered
    # with per_chat=False. For the common default (per_chat=True, per_user=True) the
    # lookup silently returned None — making ConversationTester step state assertions
    # and any direct assert-on-state usage with a conversation handler ID unreliable.
    #
    # What was broken before this fix
    # --------------------------------
    # * Tests that called call_handler with a ConversationHandler ID and then asserted
    #   `state == SomeState` would silently get None instead of the real state when
    #   per_chat=True (the default).
    # * ConversationTester.run() passes expected_state to ConversationStep; those
    #   assertions would always fail for per_chat=True conversations, forcing tests to
    #   omit expected_state entirely.
    #
    # What becomes possible after this fix
    # -------------------------------------
    # * ConversationStep(expected_state=...) assertions now work for all conversations
    #   regardless of their per_chat / per_user configuration.
    # * Tests that call call_handler with a conversation handler ID and inspect the
    #   returned state will now receive the correct in-flight state.
    #
    # Risks / notes
    # -------------
    # * This implementation handles only per_chat=True,per_user=True and
    #   per_chat=False,per_user=True (the two configurations used in this project).
    #   Other combinations fall back to (user_id,) for safety.
    # * Tests that previously asserted `state is None` on a per_chat=True conversation
    #   will now receive the real state and may need updating.
    if isinstance(handler, ConversationHandler):
        if user_id is None:
            user_id = update.effective_user.id if update.effective_user else None
        if handler.per_chat and handler.per_user:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is not None and user_id is not None:
                conv_key: tuple[int, ...] = (chat_id, user_id)
                handler_result = cast(Enum | None, handler._conversations.get(conv_key))
        elif user_id is not None:
            conv_key = (user_id,)
            handler_result = cast(Enum | None, handler._conversations.get(conv_key))
    return context, handler_result
