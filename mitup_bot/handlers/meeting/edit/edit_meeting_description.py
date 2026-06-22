import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView

from .enums import ConversationMeetingState, EditMeetingHandlerId


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DESCRIPTION_CALLBACK, callback_data=cb.EDIT_MEETING_DESCRIPTION, bindable=False
)
@with_async_session
async def callback_query_edit_meeting_description(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_edit_description_meeting")

    assert context.matches is not None

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_DESCRIPTION.parse(context.match), EditMeetingHandlerId.DESCRIPTION_CALLBACK
    )

    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit description",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DESCRIPTION,
        MeetingEditContentMessages.DESCRIPTION_ON_EXIT.get(lang=user.lang),
        cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
    )

    description = (
        meeting.description if meeting.description else MeetingDisplayMessages.DESCRIPTION_EMPTY.get(lang=user.lang)
    )
    await context.api.edit_message(
        update=update,
        view=MitupView(
            MeetingEditContentMessages.DESCRIPTION_PROMPT.get(lang=user.lang, description=description),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(lang=user.lang),
                        callback_data=cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_DESCRIPTION


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_MESSAGE, filters.TEXT, bindable=False)
@with_async_session
async def edit_description_meeting_message_handler(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into edit_description_meeting_message_handler")

    assert update.effective_message is not None

    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION) as meeting_id:
        meeting = Meetup.by_id(session, meeting_id, must_exist=True)
        meeting.description = update.effective_message.text

        session.add(meeting)
        session.flush()

        view = meeting.edit_view.with_context(
            MeetingEditContentMessages.DESCRIPTION_SUCCESS.get(description=meeting.description)
        )
        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(session=session, meeting=meeting)

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
