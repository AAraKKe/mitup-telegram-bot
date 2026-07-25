from enum import StrEnum, auto

import structlog
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import CommonMessages
from mitup_bot.views import MitupView, RenderContext, factory

log = structlog.get_logger(__name__)

# The single event name every conversation-context loss is logged under, wherever it is caught: the
# handler that recovers from it locally and the central error handler that catches the ones nobody
# recovered from. Queries key on this name plus `reason`, never on per-site prose.
CONTEXT_LOST_EVENT = "Conversation context was lost while handling the update"


class RecoveryReason(StrEnum):
    """Machine key naming the expected condition a handler recovered from without faulting."""

    CONVERSATION_CONTEXT_MISSING = auto()
    MALFORMED_CALLBACK_DATA = auto()


async def recover_from_lost_context(
    update: Update, context: TMitupContext, user: User, exc: ContextPropertyNotSetError, context_id: ContextId
) -> int:
    """Close the flow whose stored state is gone and leave the user on the main menu.

    In-memory conversation state does not survive a rolling deploy, so a flow that no longer finds
    its stored id is an expected state the handler recovers from, not a code fault: it is logged at
    the level and under the event name the central error handler uses for the losses that reach it,
    and it feeds the same `CONTEXT_LOST` series so the metric counts the condition wherever it is
    caught. The id the flow was looking for is the missing one, so the line carries the context slot
    that was empty instead.
    """
    log.warning(
        CONTEXT_LOST_EVENT,
        exc_info=exc,
        reason=RecoveryReason.CONVERSATION_CONTEXT_MISSING.value,
        context_id=context_id.value,
    )
    context.emit_metric(MetricKey.CONTEXT_LOST, 1)
    await context.api.send_message(
        update=update,
        view=factory.main_menu_view(
            guards.render_context(user, update, context),
            message=CommonMessages.CONTEXT_LOST.get(lang=user.lang),
        ),
    )
    return ConversationHandler.END


async def reply_rich_message_not_supported(
    ctx: RenderContext, update: Update, context: TMitupContext, view: MitupView
) -> None:
    """Reply to a rich message by re-prompting the current step.

    *view* is the step's own re-prompt view; the not-supported notice is prepended so the user
    keeps their buttons and reads the step as still open. The rich-message feature metric is
    recorded here so every per-step rich handler emits it exactly once.
    """
    view.with_context(CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.get(lang=ctx.lang))
    await context.api.send_message(update=update, view=view)
    context.put_feature_metric(Feature.RICH_MESSAGE)
