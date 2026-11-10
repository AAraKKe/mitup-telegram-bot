from enum import StrEnum, auto

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Message, Update
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


class Screen(StrEnum):
    """A navigation-only screen, named as the `screen` facet of `Screen shown`."""

    MAIN_MENU = auto()
    HELP = auto()
    SETTINGS = auto()
    PRIVACY = auto()


class ScreenDelivery(StrEnum):
    """Whether a screen replaced the tapped message or arrived as a new one.

    The two are different user-visible outcomes: the `SEND` variants exist so a standalone message
    keeps its buttons and an in-flight conversation elsewhere is left undisturbed.
    """

    EDIT = auto()
    SEND = auto()


def log_screen_shown(user: User, screen: Screen, delivery: ScreenDelivery):
    """Record a screen that only navigates, so a session is not a gap between mutations."""
    log.info("Screen shown", user_id=user.db_id, screen=screen.value, delivery=delivery.value)


async def recover_from_lost_context(
    session: AsyncSession,
    update: Update,
    context: TMitupContext,
    user: User,
    exc: ContextPropertyNotSetError,
    context_id: ContextId,
) -> int:
    """Close the flow whose stored state is gone and leave the user on the main menu.

    This is the backstop for a step whose stored context is missing while its conversation is live;
    a restart cannot cause it, as conversation state and user data sit in memory and go together. It
    is logged and counted like the losses that reach the central error handler, naming the empty slot.
    """
    log.warning(
        CONTEXT_LOST_EVENT,
        exc_info=exc,
        reason=RecoveryReason.CONVERSATION_CONTEXT_MISSING.value,
        context_id=context_id.value,
        user_id=user.db_id,
    )
    context.emit_metric(MetricKey.CONTEXT_LOST, 1)
    await context.api.send_message(
        update=update,
        view=factory.main_menu_view(
            guards.render_context(user, update, context),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=await user.meeting_counts(session),
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
    view.with_context(CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.rich(lang=ctx.lang))
    await context.api.send_message(update=update, view=view)
    context.put_feature_metric(Feature.RICH_MESSAGE)


def message_kind(message: Message | None) -> str:
    """Name what kind of message the user sent, as a bounded machine value for a log field.

    PTB resolves the attachment; a photo arrives as a tuple of sizes rather than as one object.
    """
    if message is None:
        return "unknown"
    attachment = message.effective_attachment
    if attachment is None:
        return "text" if message.text is not None else "empty"
    if isinstance(attachment, tuple):
        return "photo"
    return type(attachment).__name__.lower()
