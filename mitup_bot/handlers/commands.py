from telegram import Update
from telegram.ext import ContextTypes

from .registry import HandlersRegistry


@HandlersRegistry.register_command
async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Hello, world."
    )
