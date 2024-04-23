import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards
from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView

from .enums import ConversationMeetingState, EditMeetingHandlerId


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DESCRIPTION_CALLBACK, callback_data=cb.EDIT_MEETING_DESCRIPTION, bindable=False
)
@with_async_session
async def callback_query_edit_meeting_description(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_edit_description_meeting")

    assert context.matches is not None

    meeting_id = cb.EDIT_MEETING_DESCRIPTION.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(EditMeetingHandlerId.DESCRIPTION_CALLBACK, cb.EDIT_MEETING_DESCRIPTION)

    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Edit description",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, meeting_id)

    await api.edit_message(
        context,
        update,
        MitupView(
            MeetingMessages.EDIT_MEETING_DESCRIPTION.get(description=meeting.description)
            if meeting.description
            else MeetingMessages.EDIT_MEETING_DESCRIPTION.get(
                full=False, description=MeetingMessages.MEETING_WITHOUT_DESCRIPTION
            ),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(),
                        callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_DESCRIPTION


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_MESSAGE, filters.TEXT, bindable=False)
@with_async_session
async def edit_description_meeting_message_handler(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into edit_description_meeting_message_handler")

    assert update.effective_message is not None

    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION) as meeting_id:
        meeting = Meetup.by_id(session, meeting_id, must_exist=True)
        meeting.description = update.effective_message.text

        session.add(meeting)
        session.flush()

        view = meeting.edit_view.with_context(
            MeetingMessages.DESCRIPTION_SET_SUCCESS.get(description=meeting.description)
        )
        await api.send_message(context, update, view)

        return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.DESCRIPTION_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DESCRIPTION_CALLBACK],
    states={
        ConversationMeetingState.EDIT_DESCRIPTION: [
            EditMeetingHandlerId.DESCRIPTION_MESSAGE,
            EditMeetingHandlerId.CANCEL,
        ],
    },
    fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT],
)
