from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.models import Meetup
from mitup_bot.utils import MeetingMessages, SettingsMessages
from mitup_bot.views import factory

from .conversations_states import ConversationSettingsState
from .registry import HandlersRegistry


class MessagesId(CallbackId):
    MESSAGE_SET_REGISTRATION_TIMEZONE = auto()
    MESSAGE_SET_SETTINGS_TIMEZONE = auto()
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()
    MESSAGE_ASK_AGAIN_ABOUT_THE_TIMEZONE = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_SET_REGISTRATION_TIMEZONE, filters.TEXT, bindable=False)
@with_async_session
async def registration_timezone_message_handler(session: Session, update: Update, context: MitupContext):
    assert update.effective_chat is not None

    if timezone := guards.message(update).text:
        user = guards.current_user(update, session)
        user.settings.timezone = timezone

        session.add(user)
        session.flush()

        message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
        view = factory.main_menu_view(message)

        await api.send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_SET_SETTINGS_TIMEZONE, filters.TEXT, bindable=False)
@with_async_session
async def settings_timezone_message_handler(session: Session, update: Update, context: MitupContext):
    assert update.effective_chat is not None

    if new_timezone := guards.message(update).text:
        user = guards.current_user(update, session)
        user.settings.timezone = new_timezone

        session.add(user)
        session.flush()

        message = SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=user.settings.timezone)
        view = factory.settings_view(message)

        await api.send_message(context, update, view)

    return ConversationHandler.END


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


@HandlersRegistry.register_message(MessagesId.MESSAGE_ASK_AGAIN_ABOUT_THE_TIMEZONE, ~filters.TEXT, bindable=False)
async def ask_again_about_the_timezone(update: Update, context: MitupContext):
    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get()

    await api.send_message(context, update, message)

    return ConversationSettingsState.TIMEZONE
