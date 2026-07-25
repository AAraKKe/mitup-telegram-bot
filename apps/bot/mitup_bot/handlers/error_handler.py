import structlog
from rich.console import Console
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Chat, Update
from telegram.error import BadRequest

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
    UserPendingDeletion,
)
from mitup_bot.keyboards import Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.translations import TranslationEngine
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

# The shared-surface rejections that carry a counter of their own. A card whose meeting is finished
# is a normal state transition and has none.
SHARED_MEETING_METRICS: dict[type, MetricKey] = {
    SharedMeetingGoneError: MetricKey.STALE_MEETING_MESSAGE,
    SharedMeetingDeniedError: MetricKey.UNAUTHORIZED_MEETING_CALLBACK,
}


@db.with_session
async def handle_inactive_user(session: AsyncSession, context: TMitupContext, tg_user_id: int):
    # InactiveUserInteraction carries the TELEGRAM user id (see the api_wrapper raise sites);
    # filtering on the internal primary key silently matched nothing — or the wrong user.
    if (
        user := (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
    ) and user.mark_inactive():
        context.emit_metric(MetricKey.INACTIVE_USER_SET, 1, include_handler_properties=False)


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
        log.debug("Failed to deliver the pending-deletion notice to the user.", exc_info=True)


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

    An update whose card is an inline message carries no chat at all (Telegram sends only the
    `inline_message_id`), and an inline card can sit in any chat — so an absent chat is treated as
    "not the bot's chat" and the banner keeps no keyboard.
    """
    chat = update.effective_chat
    return factory.main_menu_back_rows(lang) if chat is not None and chat.type == Chat.PRIVATE else []


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
            text=MeetingDisplayMessages.DELETED_BANNER.get(lang=error.lang),
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


async def handle_meeting_access_error(context: TMitupContext, error: MeetingAccessError):
    """Answer a caller `guards.meeting` stopped, and close the interaction as a completed one.

    Acting on a meeting that is gone, inactive or somebody else's is what a stale button produces,
    not a code fault: the rejection is counted on its own series and the interaction ends on
    `FAULT=0`, exactly like a handler that ran to completion. Only the rejections that say something
    about the caller's intent are logged; the reactivation prompt and the finished-card banner are
    normal screens.

    Delivery is best-effort, like every other render in this module: an exception raised here has no
    handler left above it and would reach `process_update` as a second, unhandled fault.
    """
    if isinstance(error, MeetingGoneError | MeetingNotOwnedError | SharedMeetingDeniedError):
        log.warning(str(error))

    update = context.telegram_update
    if update is None:
        # No update means no interaction: there is nothing to answer, and the metric properties are
        # read off the update.
        return

    if isinstance(error, MeetingNotOwnedError):
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 1, unit=MetricUnit.COUNT)
    if (shared_metric := SHARED_MEETING_METRICS.get(type(error))) is not None:
        context.emit_metric(shared_metric, include_handler_properties=False)
    context.emit_metric(MetricKey.FAULT, 0)

    try:
        await deliver_meeting_access_screen(context, update, error)
    except Exception:
        log.debug("Failed to deliver the meeting rejection screen to the user.", exc_info=True)


def should_ignore_error(error: Exception) -> bool:
    if type(error) not in SUPPRESSED_EXCEPTIONS:
        return False

    return str(error) in SUPPRESSED_EXCEPTIONS[type(error)]


@db.with_session
async def resolve_lang(session: AsyncSession, update: Update | None) -> str:
    """Best-effort lookup of the effective user's language.

    Falls back to the project default language when the update, user, or DB record is missing.
    """
    if (
        update is not None
        and update.effective_user
        and (user := await User.by_tg_user_id(session, update.effective_user.id))
    ):
        return user.lang
    return TranslationEngine.FALLBACK_LANG


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
        log.debug("Failed to deliver the redirect notification to the user.", exc_info=True)


async def handler(context: TMitupContext, error: Exception, env: Env):
    # This is the error handler that will receive every exception that is raised

    if should_ignore_error(error):
        return

    if isinstance(error, InactiveUserInteraction) and error.private:
        await handle_inactive_user(context, error.tg_user_id)
        return

    # An expected business state, not a fault: answer with the standardized alert and stop before
    # the fault metrics below.
    if isinstance(error, UserPendingDeletion):
        await handle_pending_deletion_user(context, error)
        return

    # The meeting guard's rejections carry their own screen, so they are answered here and stop
    # before the fault metrics below.
    if isinstance(error, MeetingAccessError):
        await handle_meeting_access_error(context, error)
        return

    # Context loss is an expected consequence of holding conversation state in memory (a rolling
    # deploy wipes user_data mid-flow, or flow-shaped input arrives with no active flow), not a code
    # fault. It gets its own metric and bypasses the fault alarms below; the user is redirected to the
    # main menu with a friendly note explaining their saved data is safe.
    if isinstance(error, ContextPropertyNotSetError):
        log.warning("Conversation context was lost while handling the update", exc_info=error)
        context.emit_metric(MetricKey.CONTEXT_LOST, 1)
        await notify_guard_error(context, CommonMessages.CONTEXT_LOST)
        return

    # One dimensionless FAULT for the invocation — the handler identity rides as an EMF property —
    # so the series the infra alarms read carries exactly one value per fault. The exception class
    # is a property rather than a metric name: a name minted from a runtime value opens a
    # separately-billed CloudWatch series per class, and none of them is charted or alarmed.
    error_type = f"{type(error).__module__}.{type(error).__qualname__}"
    # The failure path deliberately carries the trigger and its context (what the user did, plus
    # who/where — see fault_fields_from_update): the lean happy-path properties are not enough to
    # debug a real fault, and log retention owns the PII lifecycle. `UpdatePayload` is a distinct
    # key so the lean `Update` property emitted by later metrics cannot overwrite it on the
    # shared EMF logger before the flush.
    update_payload = fault_fields_from_update(context.telegram_update) if context.telegram_update else None
    context.emit_metric(MetricKey.FAULT, 1, properties={"UpdatePayload": update_payload, "error_type": error_type})

    context.metrics.add_stack_trace()

    # The log-side record of the fault. It runs inside the handler's bound contextvars, so the line
    # carries flow/handler/update_id/tg_user_id — the correlation the EMF Fault record lacks.
    # exc_info is passed explicitly rather than read from the ambient exception state, which the
    # awaits above may have replaced.
    log.error("An error occurred while handling the update", exc_info=error, update=update_payload)

    # Any fault leaves the user stranded mid-action with no feedback, so redirect them to the main
    # menu with the generic notice. The fault metrics above already recorded the fault for alarming;
    # this delivery is best-effort and never raises, so it cannot become a second fault.
    await notify_guard_error(context)
