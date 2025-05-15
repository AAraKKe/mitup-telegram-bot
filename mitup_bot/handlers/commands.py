from enum import auto

from sqlmodel import Session
from telegram import Update

from mitup_bot import api, guards, views
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session

from .personal_filters import UserExistFilter
from .registry import HandlersRegistry


class CommandsId(CallbackId):
    START_WITH_EXISTING_USER = auto()
    MAIN_MENU = auto()


@HandlersRegistry.register_command(
    CommandsId.START_WITH_EXISTING_USER,
    command="start",
    filters=UserExistFilter(),
)
@with_async_session
async def command_start_with_existing_user(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)
    view = views.factory.main_menu_view(lang=user.lang)

    await api.send_message(context=context, update=update, view=view)


@HandlersRegistry.register_command(CommandsId.MAIN_MENU, command="main_menu")
async def command_go_to_main_menu(update: Update, context: MitupContext):
    await command_start_with_existing_user(update, context)
