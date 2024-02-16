import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from mitup_bot import messages
from mitup_bot.api import edit_message_view, send_message_view
from mitup_bot.models import User
from mitup_bot.views.views import change_settings_element_view, main_menu_view, settings_view

from .conversations_states import ConversationSettingsState
from .registry import HandlersRegistry


@HandlersRegistry.register_callback_query("callback_query_settings", pattern="^settings$", bindable=True)
async def callback_query_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    logging.info("Enter into callback_query_settings")

    view = settings_view()

    await edit_message_view(context, update, view)


@HandlersRegistry.register_callback_query(
    "callback_query_settings_timezone", pattern="^global_timezone$", bindable=False
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
            message = messages.SET_TIMEZONE_SETTINGS.substitute(timezone=user.settings.timezone)

            view = change_settings_element_view(message)

            await send_message_view(context, update, view)

            return ConversationSettingsState.TIMEZONE
        else:
            raise RuntimeError("User not found")


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
