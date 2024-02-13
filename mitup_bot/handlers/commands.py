from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from mitup_bot.api import send_message, send_message_view
from mitup_bot.models import Settings, User
from mitup_bot.views.views import main_menu_view

from .conversations_states import ConversationSettingsState
from .personal_filters import UserExistFilter
from .registry import HandlersRegistry


@HandlersRegistry.register_command(
    "start_command_with_new_user", command="start", filters=~UserExistFilter(), bindable=False
)
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
                settings=Settings(),
            )
            user.create()
            message = f"Welcome to Mitup Bot {user.first_name}! Please, tell me your timezone."

            await send_message(context, update, message)

            return ConversationSettingsState.TIMEZONE


@HandlersRegistry.register_command("start_command_with_existing_user", command="start", filters=UserExistFilter())
async def command_start_with_existing_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    if update.effective_message is not None and update.effective_user is not None:
        await send_message_view(context, update, view)


@HandlersRegistry.register_command("cancel_command", command="cancel", bindable=False)
async def command_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await send_message(context, update, "Bye!")

    return ConversationHandler.END
