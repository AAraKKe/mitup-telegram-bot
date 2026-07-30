import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import RecoveryReason
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views

from .enums import EditMeetingHandlerId
from .utils import cleanup_states

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.EDIT, callback_data=cb.EDIT_MEETING, bindable=True)
@with_session
async def callback_query_edit_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(cb.EDIT_MEETING.parse(context.match), EditMeetingHandlerId.EDIT)

    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "Edit meeting", context)

    log.info("Meeting edit hub opened", user_id=user.db_id)

    # Only allow editing the meeting if the meeting belongs to the user
    await context.api.edit_message(update=update, view=meeting_views.edit_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CANCEL, callback_data=cb.EDIT_MEETING_CANCEL, bindable=False
)
@with_session
async def callback_query_cancel_edit_meeting(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    """If at any point the user clicks on Cancel we should get back to the Edit meeting view"""
    user = await guards.current_user(update, session)

    try:
        meeting_id = guards.valid_callback_data(
            cb.EDIT_MEETING_CANCEL.parse(context.match), EditMeetingHandlerId.CANCEL
        ).id
    except MalformedCallbackData as exc:
        # Callback data is client-supplied, so a cancel button can arrive without the meeting it
        # refers to. There is no edit screen to return to, so the flow is closed and the user lands
        # on the main menu — a recovered interaction, not a fault.
        cleanup_states(context)
        # The meeting id is exactly what could not be recovered, so the payload it failed to parse
        # is the only thing that identifies what the user tapped. It is a bounded, client-supplied
        # token, not authored text — the same field the registry's entry line carries.
        log.warning(
            "Malformed callback data while cancelling meeting edit",
            exc_info=exc,
            reason=RecoveryReason.MALFORMED_CALLBACK_DATA.value,
            user_id=user.db_id,
            callback_data=update.callback_query.data if update.callback_query is not None else None,
            conversation_state_cleared=True,
        )
        await context.api.edit_message(
            update=update, view=factory.main_menu_view(guards.render_context(user, update, context))
        )
        return ConversationHandler.END

    meetup = await guards.meeting(
        session,
        user,
        meeting_id,
        "Cancel edit meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    log.info("Meeting edit cancelled", user_id=user.db_id, reason="user_cancelled", conversation_state_cleared=True)

    await context.api.edit_message(update=update, view=meeting_views.edit_view(meetup))

    # Cleanup any possible state set by any handler related with editing the meeting
    cleanup_states(context)

    return ConversationHandler.END
