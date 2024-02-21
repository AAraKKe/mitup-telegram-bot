import logging
from enum import auto

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from mitup_bot.api import edit_message_view, send_message_view
from mitup_bot.models import User
from mitup_bot.utils import Messages
from mitup_bot.views.views import change_settings_element_view, main_menu_view, settings_view

from .conversations_states import ConversationSettingsState
from .registry import CallbackId, HandlersRegistry


class CallbackQueryId(CallbackId):
    CALLBACK_QUERY_SETTINGS = auto()
    CALLBACK_QUERY_SETTINGS_TIMEZONE = auto()
    CALLBACK_QUERY_CANCEL_SETTINGS = auto()
    CALLBACK_QUERY_MAIN_MENU = auto()


@HandlersRegistry.register_callback_query(CallbackQueryId.CALLBACK_QUERY_SETTINGS, pattern="^settings$", bindable=True)
async def callback_query_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    logging.info("Enter into callback_query_settings")

    view = settings_view()

    await edit_message_view(context, update, view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CALLBACK_QUERY_SETTINGS_TIMEZONE, pattern="^global_timezone$", bindable=False
)
async def callback_query_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    logging.info("Enter into callback_query_settings_timezone")

    with User.open_session():
        if user := User.find_by_tg_user_id(update.effective_user.id):
            message = Messages.SET_TIMEZONE_SETTINGS.get(timezone=user.settings.timezone)

            view = change_settings_element_view(message)

            await send_message_view(context, update, view)

            return ConversationSettingsState.TIMEZONE
        else:
            raise RuntimeError("User not found")


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CALLBACK_QUERY_CANCEL_SETTINGS, pattern="^cancel_settings$", bindable=False
)
async def callback_query_cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = settings_view()

    await send_message_view(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CALLBACK_QUERY_MAIN_MENU, pattern="^main_menu$", bindable=True
)
async def callback_query_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    await edit_message_view(context, update, view)
