from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.models import Meetup
from mitup_bot.utils import MeetingMessages
from mitup_bot.views import factory

from .registry import HandlersRegistry


class MessagesId(CallbackId):
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_CREATE_MEETING, filters.TEXT, bindable=False)
@with_async_session
async def create_meeting_message_handler(session: Session, update: Update, context: MitupContext):
    assert update.effective_chat is not None

    if title := guards.message(update).text:
        user = guards.current_user(update, session)
        meetup = Meetup(title=title, owner=user)

        session.add(meetup)
        session.flush()

        message = MeetingMessages.CREATED_SUCCESS.get(title=meetup.title)
        view = meetup.edit_view.with_context(message)

        await api.send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_WITHOUT_TEXT, ~filters.TEXT | filters.COMMAND, bindable=False)
async def filter_messages_without_text(update: Update, context: MitupContext):
    context.clean_all_user_data()

    view = factory.main_menu_view()

    await api.send_message(context, update, view)

    return ConversationHandler.END
