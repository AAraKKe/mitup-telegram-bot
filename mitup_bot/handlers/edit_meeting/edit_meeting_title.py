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
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView

from .enums import ConversationMeetingState, EditMeetingHandlerId


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.TITLE_CALLBACK, callback_data=cb.EDIT_MEETING_TITLE, bindable=False
)
@with_async_session
async def callback_query_edit_meeting_title(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_edit_title_meeting")

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_TITLE.parse(context.match), EditMeetingHandlerId.TITLE_CALLBACK
    ).id

    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Edit title",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TITLE,
        MeetingMessages.EDIT_MEETING_TITLE_ON_EXIT.get(lang=user.lang),
        cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
    )

    await context.api.edit_message(
        update=update,
        view=MitupView(
            description=MeetingMessages.EDIT_MEETING_TITLE.get(title=meeting.title),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(lang=user.lang),
                        callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_TITLE


@HandlersRegistry.register_message(EditMeetingHandlerId.TITLE_MESSAGE, filters.TEXT, bindable=False)
@with_async_session
async def edit_title_meeting_message_handler(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into edit_title_meeting_message_handler")

    assert update.effective_message is not None and update.effective_message.text is not None

    with context.meeting_id(ContextId.EDIT_MEETING_TITLE) as meeting_id:
        meeting = Meetup.by_id(session, meeting_id, must_exist=True)
        meeting.title = update.effective_message.text

        session.add(meeting)
        session.flush()

        view = meeting.edit_view.with_context(MeetingMessages.TITLE_SET_SUCCESS.get(title=meeting.title))
        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(session=session, meeting=meeting)

        return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.TITLE_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.TITLE_CALLBACK],
    states={
        ConversationMeetingState.EDIT_TITLE: [
            EditMeetingHandlerId.TITLE_MESSAGE,
            EditMeetingHandlerId.CANCEL,
        ],
    },
    fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT],
)
