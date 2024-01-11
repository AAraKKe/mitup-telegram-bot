from telegram import Update
from telegram.ext import ContextTypes, filters, ConversationHandler

from .registry import HandlersRegistry
from mitup_bot.models import User


@HandlersRegistry.register_message("set_timezone_settings", filters.TEXT, bindable=False)
async def timezone_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    with User.open_session():
        if update.effective_user is not None and update.effective_message is not None:
            if user := User.find_by_tg_user_id(update.effective_user.id):
                if update.effective_message.text is not None:
                    user.settings.timezone = update.effective_message.text
                    user.update()

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Perfect! Your timezone is {user.settings.timezone}",
                )

                return ConversationHandler.END
