from enum import auto

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_async_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.utils.mitup_types import TMitupContext

from .personal_filters import UserExistFilter
from .registry import HandlersRegistry


class CommandsId(HandlerId):
    START_WITH_EXISTING_USER = auto()
    MAIN_MENU = auto()


@HandlersRegistry.register_command(
    CommandsId.START_WITH_EXISTING_USER,
    command="start",
    filters=UserExistFilter(),
)
@with_async_session
async def command_start_with_existing_user(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)
    view = views.factory.main_menu_view(lang=user.lang)

    await context.api.send_message(update=update, view=view)


@HandlersRegistry.register_command(CommandsId.MAIN_MENU, command="main_menu")
async def command_go_to_main_menu(update: Update, context: TMitupContext):
    await command_start_with_existing_user(update, context)
