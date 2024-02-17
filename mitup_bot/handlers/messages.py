from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from mitup_bot.api import send_message_view
from mitup_bot.models import User
from mitup_bot.utils import Messages
from mitup_bot.views.views import main_menu_view, settings_view

from .registry import HandlersRegistry


@HandlersRegistry.register_message("set_registration_timezone_settings", filters.TEXT, bindable=False)
async def registration_timezone_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    if update.effective_message.text is None:
        raise RuntimeError("Effective message text not set")

    with User.open_session():
        if user := User.find_by_tg_user_id(update.effective_user.id):
            user.settings.timezone = update.effective_message.text
            user.update()

            message = Messages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
            view = main_menu_view(message)

            await send_message_view(context, update, view)

        return ConversationHandler.END


@HandlersRegistry.register_message("set_timezone_settings", filters.TEXT, bindable=False)
async def settings_timezone_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    if update.effective_message.text is None:
        raise RuntimeError("Effective message text not set")

    with User.open_session():
        if user := User.find_by_tg_user_id(update.effective_user.id):
            user.settings.timezone = update.effective_message.text
            user.update()

            message = Messages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=user.settings.timezone)
            view = settings_view(message)

            await send_message_view(context, update, view)

        return ConversationHandler.END
