from telegram import Update
from telegram.ext import ContextTypes

from .registry import HandlersRegistry
from mitup_bot.models import User, Settings

@HandlersRegistry.register_command("start_command")
async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    print("AAAAAAAAAAAAAA")
    user = User(
            first_name=update.effective_user.first_name,
            tg_user_id=update.effective_user.id,
            last_name=update.effective_user.last_name,
            username=update.effective_user.username,
            settings=Settings(timezone="Jaen")
            ) 
    user.create()


    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=f"Hello {user.first_name}"
    )
