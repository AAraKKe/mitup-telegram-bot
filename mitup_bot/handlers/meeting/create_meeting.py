import logging
from enum import Enum, auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.monitoring.metric_keys import Feature
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb

from ..main_menu.show_main_menu import callback_query_main_menu
from .enums import MeetingHandlerId


class ConversationMeetingState(Enum):
    TITLE = auto()


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CALLBACK, callback_data=cb.CREATE_MEETING, bindable=False
)
@with_async_session
async def callback_query_create_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_create_meeting")

    user = guards.current_user(update, session)
    view = views.factory.create_meeting_view(lang=user.lang)

    await api.edit_message(context=context, update=update, view=view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_async_session
async def create_meeting_message_handler(session: Session, update: Update, context: MitupContext):
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

    await api.send_message(context=context, update=update, view=view)

    context.put_feature_metric(Feature.CREATE_MEETING)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK, callback_data=cb.CANCEL_CREATE_MEETING, bindable=False
)
async def callback_query_cancel_meeting(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_cancel_meeting")

    # Just send the user to the main menu
    await callback_query_main_menu(update, context)

    context.put_feature_metric(Feature.CREATE_MEETING, name="Cancel")
    return ConversationHandler.END


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_TITLE_INVALID, ~filters.TEXT | filters.COMMAND, bindable=False
)
@with_async_session
async def filter_messages_without_text(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)

    await api.send_message(
        context=context,
        update=update,
        view=views.factory.create_meeting_view(
            lang=user.lang, message=MeetingMessages.INVALID_TITLE.get(lang=user.lang)
        ),
    )

    return ConversationMeetingState.TITLE


HandlersRegistry.register_conversation_handler(
    MeetingHandlerId.CREATE_MEETING_CONVERSATION,
    entry_points_handler_names=[MeetingHandlerId.CREATE_MEETING_CALLBACK],
    states={
        ConversationMeetingState.TITLE: [
            MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
            MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK,
            MeetingHandlerId.CREATE_MEETING_TITLE_INVALID,
        ],
    },
    fallbacks=[],
)
