import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.models import Meetup
from mitup_bot.monitoring.metric_keys import Feature
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from ..command_enums import CommandsId
from ..main_menu.show_main_menu import callback_query_main_menu
from .enums import ConversationMeetingState, MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CALLBACK, callback_data=cb.CREATE_MEETING, bindable=False
)
@with_async_session
async def callback_query_create_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_create_meeting")

    user = guards.current_user(update, session)
    view = views.factory.create_meeting_view(lang=user.lang)

    context.store_on_exit(
        ContextId.CREATE_MEETING,
        MeetingMessages.CREATE_MEETING_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_CREATE_MEETING,
    )

    await context.api.edit_message(update=update, view=view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_async_session
async def create_meeting_message_handler(session: Session, update: Update, context: TMitupContext):
    assert update.effective_chat is not None

    title = guards.message(update).text
    assert title is not None, "There must be text in the message if we made it here"

    user = guards.current_user(update, session)
    meetup = Meetup(
        title=title,
        owner=user,
        waiting_list=user.settings.default_waiting_list,
        public=user.settings.default_public,
        allow_invitation=user.settings.default_allow_invitation,
        incognito=user.settings.default_incognito,
        show_timezone=user.settings.default_show_timezone,
    )

    session.add(meetup)
    session.flush()

    message = MeetingMessages.CREATED_SUCCESS.get(title=meetup.title, lang=user.lang)
    view = meetup.edit_view.with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.CREATE_MEETING)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK, callback_data=cb.CANCEL_CREATE_MEETING, bindable=False
)
async def callback_query_cancel_meeting(update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_cancel_meeting")

    # Just send the user to the main menu
    await callback_query_main_menu(update, context)

    context.put_feature_metric(Feature.CREATE_MEETING, name="Cancel")
    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    MeetingHandlerId.CREATE_MEETING_CONVERSATION,
    entry_points_handler_names=[
        MeetingHandlerId.CREATE_MEETING_CALLBACK,
        CommandsId.START_WITH_EXISTING_USER,  # deep link from inline mode "Create a new meeting" button
    ],
    states={
        ConversationMeetingState.TITLE: [
            MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
            MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT],
)
