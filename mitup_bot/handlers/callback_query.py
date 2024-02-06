from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .registry import HandlersRegistry
from .conversations_states import Conversation_Settings_State
from mitup_bot.models import User
from mitup_bot.views.views import settings_view, change_settings_element_view, main_menu_view
from mitup_bot.api import send_message_view, edit_message_view

import logging


@HandlersRegistry.register_callback_query("callback_query_settings", pattern="^settings$", bindable=True)
async def callback_query_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    logging.info("Enter into callback_query_settings")

    view = settings_view()

    await edit_message_view(context, update, view)


@HandlersRegistry.register_callback_query("callback_query_settings_timezone", pattern="^timezone$", bindable=False)
async def callback_query_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    logging.info("Enter into callback_query_settings_timezone")

    with User.open_session():
        if update.effective_user is not None and update.effective_message is not None:
            if user := User.find_by_tg_user_id(update.effective_user.id):
                message = (
                    f"Your timezone is set to *{user.settings.timezone}*. \n"
                    "Send me the name of your city or your location to set your "
                    "timezone or touch in *Cancel* to go back."
                )

                view = change_settings_element_view(message)

                await send_message_view(context, update, view)

                return Conversation_Settings_State.TIMEZONE


@HandlersRegistry.register_callback_query("callback_query_cancel_settings", pattern="^cancel_settings$", bindable=False)
async def callback_query_cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = settings_view()

    await send_message_view(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query("callback_query_main_menu", pattern="^main_menu$", bindable=True)
async def callback_query_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    await edit_message_view(context, update, view)
