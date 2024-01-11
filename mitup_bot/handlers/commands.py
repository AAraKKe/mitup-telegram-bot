from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .registry import HandlersRegistry
from mitup_bot.models import User, Settings
from .personal_filters import UserExistFilter
from .conversations_states import Conversation_Settings_State


@HandlersRegistry.register_command("start_command_with_new_user", command="start", filters=~UserExistFilter(), bindable=False)
async def command_start_with_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    with User.open_session():
        if update.effective_user is not None:
            user = User(
                    first_name=update.effective_user.first_name,
                    tg_user_id=update.effective_user.id,
                    last_name=update.effective_user.last_name,
                    username=update.effective_user.username,
                    settings=Settings(timezone="Jaen")  # type: ignore
                    )
            user.create()
            message = f"Welcome to Mitup Bot {user.first_name}! Please, tell me your timezone."

            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=message
            )

            return Conversation_Settings_State.TIMEZONE


@HandlersRegistry.register_command("start_command_with_existing_user", command="start", filters=UserExistFilter())
async def command_start_with_existing_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="You are already registered"
    )


@HandlersRegistry.register_command("cancel_command", command="cancel", bindable=False)
async def command_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Bye"
    )
    return ConversationHandler.END
