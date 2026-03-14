import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import EditMeetingHandlerId
from .utils import cleanup_states


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.EDIT, callback_data=cb.EDIT_MEETING, bindable=True)
@with_async_session
async def callback_query_edit_meeting(session: Session, update: Update, context: TMitupContext) -> None:
    logging.debug("Enter into callback_query_edit_meeting")

    callback_data = guards.valid_callback_data(cb.EDIT_MEETING.parse(context.match), EditMeetingHandlerId.EDIT)

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, callback_data.id, "Edit meeting", update, context)

    if meeting is None:
        return

    # Only allow editing the meeting if the meeting belongs to the user
    await context.api.edit_message(update=update, view=meeting.edit_view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CANCEL, callback_data=cb.EDIT_MEETING_CANCEL, bindable=True
)
@with_async_session
async def cancel_edit_meeting(session: Session, update: Update, context: TMitupContext) -> int:
    """If at any point the user clicks on Cancel we should get back to the Edit meeting view"""
    user = guards.current_user(update, session)

    try:
        meeting_id = guards.valid_callback_data(
            cb.EDIT_MEETING_CANCEL.parse(context.match), EditMeetingHandlerId.CANCEL
        ).id
    except MalformedCallbackData as exc:
        # If we cannot get a meeting_id from callback something went wrong.
        # Cleanup, log error and end possible conversation
        cleanup_states(context)
        logging.error(exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    logging.debug(f"Enter into cancel_edit_meeting. Meeting id: {meeting_id}")

    meetup = await guards.user_owns_meeting(user, meeting_id, "Cancel edit meeting", update, context)
    if meetup is None:
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=meetup.edit_view)

    # Cleanup any possible state set by any handler related with editing the meeting
    cleanup_states(context)

    return ConversationHandler.END
