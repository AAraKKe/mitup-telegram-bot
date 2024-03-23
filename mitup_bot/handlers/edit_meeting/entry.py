import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import api, guards
from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb

from .enums import EditMeetinHandlerId


@HandlersRegistry.register_callback_query(EditMeetinHandlerId.EDIT, callback_data=cb.EDIT_MEETING, bindable=True)
@with_async_session
async def callback_query_edit_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_edit_meeting")
    assert context.matches is not None

    user = guards.current_user(update, session)
    callback_data = cb.EDIT_MEETING.parse(context.matches[0])

    if callback_data.id is None:
        raise MalformedCallbackData(EditMeetinHandlerId.EDIT, callback_data)

    meeting = user.own_meeting(callback_data.id)
    if meeting is not None:
        # Only allow editing the meeting if the meeting belongs to the user
        await api.edit_message(context, update, meeting.edit_view)
    else:
        logging.warn(
            "User tried editing meeting that does not belong to them. "
            f"Meeting id: {callback_data.id}, user id: {user.id}"
        )


@HandlersRegistry.register_callback_query(
    EditMeetinHandlerId.CANCEL, callback_data=cb.EDIT_MEETING_CANCEL, bindable=False
)
@with_async_session
async def cancel_edit_meeting(session: Session, update: Update, context: MitupContext):
    """If at any point the user clicks on Cancel we should get back to the Edit meeting view"""
    assert context.matches is not None

    meeting_id = cb.EDIT_MEETING_CANCEL.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(EditMeetinHandlerId.CANCEL, cb.EDIT_MEETING_CANCEL)

    logging.info(f"Enter into cancel_edit_meeting. Meeting id: {meeting_id}")

    meetup = Meetup.by_id(session, meeting_id, must_exist=True)

    await api.edit_message(context, update, meetup.edit_view)

    # Cleanup any possible state set by any handler related with editing the meeting
    context.clean_user_data(
        [
            ContextId.EDIT_MEETING_LOCATION_NAME,
            ContextId.EDIT_MEETING_LOCATION_COORDINATES,
        ]
    )

    return ConversationHandler.END
