from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from mitup_bot.api import send_message
from mitup_bot.db import with_async_session
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages, SettingsMessages
from mitup_bot.views.views import main_menu_view, settings_view

from .conversations_states import ConversationSettingsState
from .registry import CallbackId, HandlersRegistry


class MessagesId(CallbackId):
    MESSAGE_SET_REGISTRATION_TIMEZONE = auto()
    MESSAGE_SET_SETTINGS_TIMEZONE = auto()
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()
    MESSAGE_ASK_AGAIN_ABOUT_THE_TIMEZONE = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_SET_REGISTRATION_TIMEZONE, filters.TEXT, bindable=False)
@with_async_session
async def registration_timezone_message_handler(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    if update.effective_message.text is None:
        raise RuntimeError("Effective message text not set")

    if user := User.by_tg_user_id(session, update.effective_user.id):
        user.settings.timezone = update.effective_message.text
        session.add(user)

        message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
        view = main_menu_view(message)

        await send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_SET_SETTINGS_TIMEZONE, filters.TEXT, bindable=False)
@with_async_session
async def settings_timezone_message_handler(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    if update.effective_message.text is None:
        raise RuntimeError("Effective message text not set")

    if user := User.by_tg_user_id(session, update.effective_user.id):
        user.settings.timezone = update.effective_message.text
        session.add(user)

        message = SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=user.settings.timezone)
        view = settings_view(message)

        await send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_CREATE_MEETING, filters.TEXT, bindable=False)
@with_async_session
async def create_meeting_message_handler(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    if update.effective_message.text is None:
        raise RuntimeError("Effective message text not set")

    if owner_user := User.by_tg_user_id(session, update.effective_user.id):
        meetup = Meetup(title=update.effective_message.text, owner=owner_user)
        session.add(meetup)
        session.flush()
        message = MeetingMessages.CREATED_SUCCESS.get(title=meetup.title)
        view = meetup.edit_view.with_context(message)

        await send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_WITHOUT_TEXT, ~filters.TEXT, bindable=False)
async def filter_messages_without_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    await send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_ASK_AGAIN_ABOUT_THE_TIMEZONE, ~filters.TEXT, bindable=False)
async def ask_again_about_the_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get()

    await send_message(context, update, message)

    return ConversationSettingsState.TIMEZONE
