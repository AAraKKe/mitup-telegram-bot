from telegram import Update
from telegram.ext import ContextTypes

from .registry import HandlersRegistry
from mitup_bot.models import User, Settings

@HandlersRegistry.register_command("start_command")
async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    print("AAAAAAAAAAAAAA")

    with User.open_session():
        if (current_user := User.find_by_tg_user_id(update.effective_user.id)) is None:
            user = User(
                    first_name=update.effective_user.first_name,
                    tg_user_id=update.effective_user.id,
                    last_name=update.effective_user.last_name,
                    username=update.effective_user.username,
                    settings=Settings(timezone="Jaen")
                    )
            user.create()
            message = f"Welcome to Mitup Bot {user.first_name}"
        else:
            message = f"Welcome back to Mitup Bot {current_user.first_name}"

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=message
    )
