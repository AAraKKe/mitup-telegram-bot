from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from mitup_bot.api import send_message
from mitup_bot.db import with_async_session
from mitup_bot.models import Settings, User
from mitup_bot.utils import SettingsMessages
from mitup_bot.views.views import main_menu_view

from .conversations_states import ConversationSettingsState
from .personal_filters import UserExistFilter
from .registry import CallbackId, HandlersRegistry


class CommandsId(CallbackId):
    COMMAND_START_WITH_NO_USER = auto()
    COMMAND_START_WITH_EXISTING_USER = auto()
    COMMAND_CANCEL = auto()
    COMMAND_MAIN_MENU = auto()


@HandlersRegistry.register_command(
    CommandsId.COMMAND_START_WITH_NO_USER, command="start", filters=~UserExistFilter(), bindable=False
)
@with_async_session
async def command_start_with_new_user(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is not None:
        user = User(
            first_name=update.effective_user.first_name,
            tg_user_id=update.effective_user.id,
            last_name=update.effective_user.last_name,
            username=update.effective_user.username,
            settings=Settings(),
        )
        session.add(user)
        message = SettingsMessages.SET_REGISTRATION_TIMEZONE.get(first_name=user.first_name)

        await send_message(context, update, message)

        return ConversationSettingsState.TIMEZONE


@HandlersRegistry.register_command(
    CommandsId.COMMAND_START_WITH_EXISTING_USER,
    command="start",
    filters=UserExistFilter(),
)
async def command_start_with_existing_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    if update.effective_message is not None and update.effective_user is not None:
        await send_message(context, update, view)


@HandlersRegistry.register_command(CommandsId.COMMAND_MAIN_MENU, command="main_menu")
async def command_go_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await command_start_with_existing_user(update, context)


@HandlersRegistry.register_command(CommandsId.COMMAND_CANCEL, command="cancel", bindable=False)
async def command_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await command_start_with_existing_user(update, context)

    return ConversationHandler.END
