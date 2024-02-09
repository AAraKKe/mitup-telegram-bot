from telegram import Update
from telegram.ext import ContextTypes, filters, ConversationHandler

from .registry import HandlersRegistry
from mitup_bot.models import User, Settings
from mitup_bot.views.views import settings_view
from mitup_bot.api import send_message, send_message_view


@HandlersRegistry.register_message("set_first_timezone_settings", filters.TEXT, bindable=False)
async def first_timezone_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    with User.open_session():
        if update.effective_user is not None and update.effective_message is not None:
            if user := User.find_by_tg_user_id(update.effective_user.id):
                if update.effective_message.text is not None:
                    user.settings.timezone = update.effective_message.text
                    user.update()

                    text = f"Perfect! Your timezone is {user.settings.timezone}"

                    await send_message(context, update, text)

        return ConversationHandler.END


@HandlersRegistry.register_message("set_timezone_settings", filters.TEXT, bindable=False)
async def second_timezone_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    # with User.open_session():
    #     if update.effective_user is not None and update.effective_message is not None:
    #         if user := User.find_by_tg_user_id(update.effective_user.id):
    #             if update.effective_message.text is not None:
    #                 user.settings.timezone = update.effective_message.text
    #                 user.update()

    #                 message = f"Your timezone has been set to: *{user.settings.timezone}* "
    #                 view = settings_view(message)

    #                 await send_message_view(context, update, view)

    with User.open_session():
        user_settings = None
        if update.effective_user is None:
            raise RuntimeError("Effective user not set")

        user_settings = User.get_settings_from_user(update.effective_user.id)

    with Settings.open_session():
        if user_settings is not None and update.effective_message is not None and update.effective_message.text is not None:
            user_settings.timezone = update.effective_message.text
            user_settings.update()

            message = f"Your timezone has been set to: *{user_settings.timezone}* "
            view = settings_view(message)

            await send_message_view(context, update, view)

        return ConversationHandler.END
