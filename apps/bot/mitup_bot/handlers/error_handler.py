from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any

import structlog
from rich.console import Console
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Chat, Update
from telegram.error import BadRequest, Forbidden

from mitup_bot import db, guards
from mitup_bot.config import Env
from mitup_bot.custom_context import fault_fields_from_update
from mitup_bot.exceptions import (
    ContextPropertyNotSetError,
    InactiveUserInteraction,
    MeetingAccessError,
    MeetingGoneError,
    MeetingInactiveOwnerError,
    MeetingNotOwnedError,
    SharedMeetingDeniedError,
    SharedMeetingError,
    SharedMeetingFinishedError,
    SharedMeetingGoneError,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.handlers.utils import CONTEXT_LOST_EVENT, RecoveryReason
from mitup_bot.keyboards import Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import InactiveReason
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.translations import TranslationEngine, locale_for_language_code
from mitup_bot.utils.messages import CommonMessages, MeetingDisplayMessages, PrivacyMessages
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views

console = Console()

log = structlog.get_logger(__name__)

# Errors that can be suppressed and ignored
SUPPRESSED_EXCEPTIONS: dict[type, set[str]] = {
    BadRequest: {
        # This happens when a message is deleted
        "Message to edit not found",
    }
}

# The shared-surface rejections that carry a counter of their own. A tap on a card whose meeting is
# gone or finished is a normal state transition, so only the unauthorised tap — a claim the caller
# never held — is metered; `Rejected meeting action` carries the rest under its `reason`.
SHARED_MEETING_METRICS: dict[type, MetricKey] = {
    SharedMeetingDeniedError: MetricKey.UNAUTHORIZED_MEETING_CALLBACK,
}


class MeetingRejectionReason(StrEnum):
    """Machine key telling the six meeting rejections apart under one event name."""

    MEETING_NOT_FOUND = auto()
    MEETING_NOT_OWNED = auto()
    MEETING_INACTIVE_AND_OWNED = auto()
    SHARED_MESSAGE_STALE = auto()
    SHARED_TAP_UNAUTHORIZED = auto()
    SHARED_MEETING_FINISHED = auto()


MEETING_REJECTION_REASONS: dict[type, MeetingRejectionReason] = {
    MeetingGoneError: MeetingRejectionReason.MEETING_NOT_FOUND,
    MeetingNotOwnedError: MeetingRejectionReason.MEETING_NOT_OWNED,
    MeetingInactiveOwnerError: MeetingRejectionReason.MEETING_INACTIVE_AND_OWNED,
    SharedMeetingGoneError: MeetingRejectionReason.SHARED_MESSAGE_STALE,
    SharedMeetingDeniedError: MeetingRejectionReason.SHARED_TAP_UNAUTHORIZED,
    SharedMeetingFinishedError: MeetingRejectionReason.SHARED_MEETING_FINISHED,
}

# The reactivation prompt and the finished-card banner are screens a healthy system produces, so
# they are narrative. The other four mean somebody tapped a button that should not have worked.
MEETING_REJECTIONS_AT_INFO: frozenset[type] = frozenset({MeetingInactiveOwnerError, SharedMeetingFinishedError})


@dataclass(frozen=True)
class FaultOutcome:
    """How the invocation ended, handed back to the wrapper that owns the `FAULT` emission.

    `FAULT` has a single writer per invocation, so a classification made here travels back as a
    value instead of a second write to the series. `properties` carries whatever only this module
    knows about the failure — the exception class on a genuine fault.
    """

    value: float
    properties: dict[str, Any] | None = None


# Every classification this module makes short of a genuine fault closes the interaction as a
# completed one, exactly like a handler that ran to the end.
HANDLED_OUTCOME = FaultOutcome(0)


def write_phase_fields(state: db.WriteState | None) -> dict[str, object]:
    """Where a failure sits relative to the transaction, when it happened inside a write.

    Without them the fault line reads as a failed action for a write that landed and only failed
    to render afterwards. A read-only handler runs in no critical section and carries neither.
    """
    if state is None:
        return {}
    return {"phase": state.phase.value, "committed": state.committed}


def fault_error_type(error: Exception) -> str:
    """Name `error`'s class the way a fault record carries it.

    The class rides as a property rather than as part of the metric name: a name minted from a
    runtime value opens a separately-billed CloudWatch series per class, and none of them is charted
    or alarmed. Shared with the registry, which names the same fault when this module cannot.
    """
    return f"{type(error).__module__}.{type(error).__qualname__}"


def in_bot_chat(update: Update) -> bool:
    """Whether `update` came from the user's own chat with the bot.

    An update acting on an inline message carries no chat at all (Telegram sends only the
    `inline_message_id`), and an inline message can sit in any chat — so an absent chat counts as
    "not the bot's chat".
    """
    chat = update.effective_chat
    return chat is not None and chat.type == Chat.PRIVATE


@db.with_session
async def handle_inactive_user(session: AsyncSession, tg_user_id: int):
    """Flip the user who just proved unreachable from MEMBER to LEFT.

    The transition is what the purge job later acts on, so all three outcomes are recorded: the
    flip itself, a row the lookup could not find, and a user who was already past MEMBER. The
    event name is shared with every other producer of MEMBER→LEFT so one filter finds them all.
    """
    # InactiveUserInteraction carries the TELEGRAM user id (see the api_wrapper raise sites);
    # filtering on the internal primary key silently matched nothing — or the wrong user.
    user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
    if user is None:
        log.warning("Could not mark an unknown user inactive", tg_user_id=tg_user_id, reason="user_row_not_found")
        return

    previous_status = user.status
    if not user.mark_inactive(InactiveReason.INTERACTION_UNREACHABLE):
        log.info(
            "Skipped marking user inactive",
            tg_user_id=tg_user_id,
            reason="already_inactive",
            previous_status=previous_status.value,
        )
        return


async def handle_pending_deletion_user(context: TMitupContext, error: UserPendingDeletion):
    """Answer the interaction with the pending-deletion alert and do nothing else.

    The account is about to be purged, so no screen is rebuilt and no state is touched. Delivery is
    best-effort like ``notify_guard_error``: a failure here must not escape as a second fault.
    """
    update = context.telegram_update
    if update is None:
        return

    try:
        if update.callback_query is not None:
            await context.api.answer_callback_query(
                update=update, text=PrivacyMessages.PENDING_DELETION_ALERT.get_text(lang=error.lang), show_alert=True
            )
        elif update.inline_query is not None:
            await context.api.answer_inline_query(update=update, results=[], cache_time=0)
        else:
            await context.api.send_message(
                update=update, view=PrivacyMessages.PENDING_DELETION_ALERT.get(lang=error.lang)
            )
    except Exception:
        log.warning(
            "Failed to deliver the pending-deletion notice to the user",
            exc_info=True,
            reason="pending_deletion_notice_undeliverable",
        )


def unregistered_caller_lang(update: Update) -> str:
    """The language to answer a caller with no account row in.

    The row that would carry a stored language is gone, so the client's `language_code` is the only
    signal left; it is normalized to a supported locale exactly as it is at registration.
    """
    tg_user = update.effective_user
    return locale_for_language_code(tg_user.language_code if tg_user is not None else None)


async def handle_user_not_found(context: TMitupContext, update: Update):
    """Tell a caller in the bot's own chat how to get their account back, and touch nothing else.

    There is no account to act on, so no state is read or written. A tapped screen is replaced by
    the notice alone: every button on it resolves through the missing row and would be answered with
    this same notice, so the only way forward is the /start the text names. A message update has no
    screen of ours to replace, so the notice arrives as a fresh reply.

    Delivery is best-effort like ``notify_guard_error``: a failure here must not escape as a second
    fault.
    """
    log.warning("Rejected interaction from unregistered user", reason="user_row_not_found")

    try:
        notice = CommonMessages.ACCOUNT_NOT_FOUND.get(lang=unregistered_caller_lang(update))
        if update.callback_query is not None:
            await context.api.edit_message(update=update, view=notice)
        else:
            await context.api.send_message(update=update, view=notice)
    except Exception:
        log.warning(
            "Failed to deliver the missing-account notice to the user",
            exc_info=True,
            reason="account_notice_undeliverable",
        )


async def handle_blocked_user(context: TMitupContext, update: Update):
    """Answer a caller whose block on the bot refused the send, and record them unreachable.

    A block only closes the bot's direction: taps keep arriving while every send is refused, so
    the answer rides the callback-query alert, the only reply Telegram still delivers. Delivery is
    best-effort like every other render in this module.
    """
    log.warning("Rejected interaction from a user who blocked the bot", reason="bot_blocked_by_user")

    if (tg_user := update.effective_user) is not None:
        await handle_inactive_user(tg_user.id)

    if update.callback_query is None:
        return

    try:
        await context.api.answer_callback_query(
            update=update,
            text=CommonMessages.BOT_BLOCKED_ALERT.get_text(lang=await resolve_lang(update)),
            show_alert=True,
        )
    except Exception:
        log.warning(
            "Failed to deliver the blocked-bot alert to the user",
            exc_info=True,
            reason="blocked_alert_undeliverable",
        )


async def answer_unregistered_caller(context: TMitupContext, update: Update | None):
    """Answer a caller the surface's own guard should have stopped, without writing into their chat.

    There is no account row, so there is no screen to rebuild and no main menu to send anyone back
    to; the card or query they acted on sits in a conversation that is not the bot's, so it is left
    untouched. A callback query carries an alert only that caller sees, an inline query can only be
    answered with results and a caller with no account has none, and any other update has no answer
    that would not post into somebody else's chat.

    Best-effort like every other render in this module: a failure here has no handler left above it
    and would reach `process_update` as a second, unhandled fault.
    """
    if update is None:
        return

    try:
        if update.callback_query is not None:
            await context.api.answer_callback_query(
                update=update,
                text=CommonMessages.UNEXPECTED_ERROR_ALERT.get_text(lang=unregistered_caller_lang(update)),
                show_alert=True,
            )
        elif update.inline_query is not None:
            await context.api.answer_inline_query(update=update, results=[], cache_time=0)
    except Exception:
        log.warning(
            "Failed to deliver the unexpected-error alert to the user",
            exc_info=True,
            reason="unexpected_error_alert_undeliverable",
        )


def meeting_access_view(error: MeetingAccessError, ctx: RenderContext) -> MitupView:
    """Build the screen that answers a meeting rejection, one per rejection reason.

    A rejection raised mid-flow carries a `flow_context`, which is added as one sentence at the end of
    the screen's description: the screen alone says what happened to the meeting, not which of the
    user's own steps it interrupted. It is the same single reply either way — a rejection without a
    flow context renders exactly the screen its reason stands for.
    """
    if isinstance(error, MeetingGoneError):
        view = factory.deleted_meeting_view(ctx, back_rows=error.keyboard)
    elif isinstance(error, MeetingInactiveOwnerError):
        view = factory.reactivation_prompt_view(ctx, meeting_id=error.meeting_id, back_rows=error.keyboard)
    else:
        view = factory.main_menu_view(ctx)

    if error.flow_context is None:
        return view
    return view.with_footnote(error.flow_context.get(lang=error.lang))


def shared_banner_keyboard(update: Update, lang: str) -> Keyboard:
    """The rows a state banner carries in the chat the tapped card sits in.

    In the bot's own chat the banner is the whole screen the user is left looking at, so it offers the
    way back to the main menu. Anywhere else the card sits in a conversation between people: the
    banner replaces it in place, keyboard-free, and the main menu is not a screen that belongs there.
    """
    return factory.main_menu_back_rows(lang) if in_bot_chat(update) else []


async def deliver_shared_meeting_answer(context: TMitupContext, update: Update, error: SharedMeetingError):
    """Answer a tap on a meeting card the caller can no longer act through.

    A denial leaves the card alone and says so in an alert: it is answered with the deleted-meeting
    copy, so a rejection reveals nothing about the meeting's state — only that the id resolves, which
    sequential ids give away anyway. The other two rejections mean the card itself is out of date, so
    it is replaced by the banner naming the state its meeting is in, keyboarded by
    `shared_banner_keyboard`.
    """
    if isinstance(error, SharedMeetingDeniedError):
        await context.api.answer_callback_query(
            update=update,
            text=MeetingDisplayMessages.DELETED_BANNER.get_text(lang=error.lang),
            show_alert=True,
        )
        return

    banner = (
        MeetingDisplayMessages.FINISHED_BANNER
        if isinstance(error, SharedMeetingFinishedError)
        else MeetingDisplayMessages.DELETED_BANNER
    )
    await context.api.edit_message(
        update=update,
        view=MitupView(description=banner.get(lang=error.lang), keyboard=shared_banner_keyboard(update, error.lang)),
    )


async def deliver_meeting_access_screen(context: TMitupContext, update: Update, error: MeetingAccessError):
    """Send the rejection screen in the shape this update can carry.

    A shared surface answers on the card that was tapped, whatever the update looks like. Everywhere
    else the shape follows the update: a callback query replaces the screen the button sits on, a
    message update has no message of ours to replace so the rejection arrives as a fresh reply, and
    an inline query can only carry results, so it is answered with the unavailable card.
    """
    if isinstance(error, SharedMeetingError):
        await deliver_shared_meeting_answer(context, update, error)
        return

    if update.inline_query is not None:
        await context.api.answer_inline_query(
            update=update, results=[meeting_views.unavailable_inline_view(error.lang)], cache_time=0
        )
        return

    view = meeting_access_view(error, RenderContext(lang=error.lang, is_admin=guards.is_admin(update, context)))
    if update.callback_query is not None:
        await context.api.edit_message(update=update, view=view)
    else:
        await context.api.send_message(update=update, view=view)


def log_meeting_rejection(error: MeetingAccessError):
    """Record the rejection under one constant event name, whichever screen answers it.

    An interpolated message would make the `event` value itself vary and leave the rejections
    uncountable, so the six are told apart by `reason`, and the level separates an anomaly from a
    screen the system is supposed to produce.
    """
    logger = log.info if type(error) in MEETING_REJECTIONS_AT_INFO else log.warning
    logger(
        "Rejected meeting action",
        meeting_id=error.meeting_id,
        action=error.action,
        error_type=fault_error_type(error),
        reason=MEETING_REJECTION_REASONS[type(error)].value,
    )


async def handle_meeting_access_error(context: TMitupContext, error: MeetingAccessError):
    """Answer a caller `guards.meeting` stopped.

    Acting on a meeting that is gone, inactive or somebody else's is what a stale button produces,
    not a code fault: the rejection is counted on its own series and the interaction closes as a
    completed one.

    Delivery is best-effort, like every other render in this module: an exception raised here has no
    handler left above it and would reach `process_update` as a second, unhandled fault.
    """
    log_meeting_rejection(error)

    update = context.telegram_update
    if update is None:
        # No update means no interaction: there is nothing to answer.
        return

    if isinstance(error, MeetingNotOwnedError):
        context.emit_metric(MetricKey.MEETING_NOT_OWNED, 1, unit=MetricUnit.COUNT)
    if (shared_metric := SHARED_MEETING_METRICS.get(type(error))) is not None:
        context.emit_metric(shared_metric, include_handler_properties=False)

    try:
        await deliver_meeting_access_screen(context, update, error)
    except Exception:
        log.warning(
            "Failed to deliver the meeting rejection screen to the user",
            exc_info=True,
            reason="meeting_rejection_undeliverable",
        )


def should_ignore_error(error: Exception) -> bool:
    if type(error) not in SUPPRESSED_EXCEPTIONS:
        return False

    return str(error) in SUPPRESSED_EXCEPTIONS[type(error)]


@db.with_session
async def stored_lang(session: AsyncSession, update: Update | None) -> str | None:
    """Best-effort lookup of the language on the caller's account row.

    `None` means there was no row to read one from, which is a different answer from a row whose
    language happens to be the project default: a caller with no row still has the client's
    `language_code` left to fall back to, and only this shape lets a caller distinguish the two.
    """
    if (
        update is not None
        and update.effective_user
        and (user := await User.by_tg_user_id(session, update.effective_user.id))
    ):
        return user.lang
    return None


async def resolve_lang(update: Update | None) -> str:
    """Best-effort lookup of the effective user's language.

    Falls back to the project default language when the update, user, or DB record is missing.
    """
    return await stored_lang(update) or TranslationEngine.FALLBACK_LANG


async def send_guard_notification(context: TMitupContext, update: Update, lang: str, message: CommonMessages):
    # This runs in the best-effort guard-error path (wrapped in try/except upstream), and both
    # update and context are available here, so an admin keeps seeing the Admin row on the redirect.
    view = factory.main_menu_view(
        RenderContext(lang=lang, is_admin=guards.is_admin(update, context)),
        message=message.get(lang=lang),
    )
    if update.callback_query is not None:
        await context.api.answer_callback_query(update=update, text="", show_alert=False)
    await context.api.send_message(update=update, view=view)


async def notify_guard_error(context: TMitupContext, message: CommonMessages = CommonMessages.UNEXPECTED_ERROR):
    """Send the user back to the main menu with ``message``; never raises.

    This runs inside the last-resort error handler, so the whole path — lang resolution, view build,
    message render, callback answer and send — is best-effort. Any failure (missing chat, blocked bot,
    absent translation file, DB error) is swallowed and logged, since re-raising here would escape to
    ``process_update`` as an unhandled second fault. Both the guard-error and context-loss paths reuse
    it, passing the message that fits the situation.
    """
    update = context.telegram_update
    if update is None:
        return

    try:
        lang = await resolve_lang(update)
        await send_guard_notification(context, update, lang, message)
    except Exception:
        log.warning(
            "Failed to deliver the redirect notification to the user",
            exc_info=True,
            reason="redirect_undeliverable",
        )


async def handler(context: TMitupContext, error: Exception, env: Env) -> FaultOutcome:
    """Answer every exception a handler raises, and classify the invocation for its `FAULT` sample.

    The caller owns the emission, so each branch here returns the value that closes the invocation
    rather than writing the series itself — see `FaultOutcome`.
    """

    if should_ignore_error(error):
        return HANDLED_OUTCOME

    if isinstance(error, InactiveUserInteraction) and error.private:
        await handle_inactive_user(error.tg_user_id)
        return HANDLED_OUTCOME

    # An expected business state, not a fault: answer with the standardized alert and stop before
    # the fault classification below.
    if isinstance(error, UserPendingDeletion):
        await handle_pending_deletion_user(context, error)
        return HANDLED_OUTCOME

    # An account row that no longer resolves is what the deletion and cleanup runs leave behind, and
    # every button its owner is still holding goes through it. In the caller's own chat with the bot
    # that is an expected business state, answered with the notice naming the /start that puts them
    # back. Every other surface answers an unregistered caller through its own
    # `guards.user_registered` before any code that can raise this runs, so one arriving from there
    # means the path was left unguarded: it falls through to the fault classification below, where
    # the caller gets an alert instead of a /start that would land in somebody else's chat.
    update = context.telegram_update
    if isinstance(error, UserNotFound) and update is not None and in_bot_chat(update):
        await handle_user_not_found(context, update)
        return HANDLED_OUTCOME

    # A Forbidden from the caller's own chat means they blocked the bot: the same expected state a
    # refused proactive DM reports as InactiveUserInteraction, except here there is a live
    # interaction to answer. From any other chat it stays a fault.
    if isinstance(error, Forbidden) and update is not None and in_bot_chat(update):
        await handle_blocked_user(context, update)
        return HANDLED_OUTCOME

    # The meeting guard's rejections carry their own screen, so they are answered here and stop
    # before the fault classification below.
    if isinstance(error, MeetingAccessError):
        await handle_meeting_access_error(context, error)
        return HANDLED_OUTCOME

    # Context loss is an expected consequence of holding conversation state in memory (a rolling
    # deploy wipes user_data mid-flow, or flow-shaped input arrives with no active flow), not a code
    # fault. It gets its own metric and bypasses the fault alarms; the user is redirected to the
    # main menu with a friendly note explaining their saved data is safe.
    if isinstance(error, ContextPropertyNotSetError):
        log.warning(CONTEXT_LOST_EVENT, exc_info=error, reason=RecoveryReason.CONVERSATION_CONTEXT_MISSING.value)
        context.emit_metric(MetricKey.CONTEXT_LOST, 1)
        await notify_guard_error(context, CommonMessages.CONTEXT_LOST)
        return HANDLED_OUTCOME

    error_type = fault_error_type(error)

    # The log-side record of the fault, and the only plane that carries the trigger and its context
    # (what the user did, plus who/where — see fault_fields_from_update): it is written once, on
    # failure, while a metric property rides every record of the flush window. The line runs inside
    # the handler's bound contextvars, so it carries flow/handler/update_id/tg_user_id — the
    # correlation the EMF Fault record lacks. exc_info is passed explicitly rather than read from
    # the ambient exception state, which the awaits above may have replaced.
    update_payload = fault_fields_from_update(update) if update else None
    log.error(
        "An error occurred while handling the update",
        exc_info=error,
        error_type=error_type,
        update_payload=update_payload,
        **write_phase_fields(db.current_write_state()),
    )

    if isinstance(error, UserNotFound):
        # The caller has no account to build a main menu for, and they are not in a chat of ours to
        # post one into, so they get the terse alert their surface can carry and nothing else.
        await answer_unregistered_caller(context, update)
    else:
        # Any other fault leaves the user stranded mid-action with no feedback, so redirect them to
        # the main menu with the generic notice. This delivery is best-effort and never raises, so it
        # cannot become a second fault.
        await notify_guard_error(context)

    return FaultOutcome(1, {"error_type": error_type})
