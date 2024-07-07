from enum import auto

from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import api, views
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext

from .personal_filters import UserExistFilter
from .registry import HandlersRegistry


class CommandsId(CallbackId):
    START_WITH_EXISTING_USER = auto()
    CANCEL = auto()
    MAIN_MENU = auto()


@HandlersRegistry.register_command(
    CommandsId.START_WITH_EXISTING_USER,
    command="start",
    filters=UserExistFilter(),
)
async def command_start_with_existing_user(update: Update, context: MitupContext):
    view = views.factory.main_menu_view()

    await api.send_message(context=context, update=update, view=view)


@HandlersRegistry.register_command(CommandsId.MAIN_MENU, command="main_menu")
async def command_go_to_main_menu(update: Update, context: MitupContext):
    await command_start_with_existing_user(update, context)


@HandlersRegistry.register_command(CommandsId.CANCEL, command="cancel", bindable=False)
async def command_cancel(update: Update, context: MitupContext):
    await command_start_with_existing_user(update, context)

    return ConversationHandler.END
