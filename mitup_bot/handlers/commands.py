from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_async_session
from mitup_bot.utils.mitup_types import TMitupContext

from .command_enums import CommandsId
from .personal_filters import UserExistFilter
from .registry import HandlersRegistry


@HandlersRegistry.register_command(
    CommandsId.START_WITH_EXISTING_USER,
    command="start",
    filters=UserExistFilter(),
)
@with_async_session
async def command_start_with_existing_user(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    if context.args and context.args[0] == "inline":
        # Local import to avoid circular dependency at load time: commands.py must be fully
        # loaded before create_meeting.py registers the conversation handler that references
        # CommandsId.START_WITH_EXISTING_USER as an entry point.
        from mitup_bot.handlers.meeting.enums import ConversationMeetingState

        view = views.factory.create_meeting_view(lang=user.lang)
        await context.api.send_message(update=update, view=view)
        return ConversationMeetingState.TITLE

    view = views.factory.main_menu_view(lang=user.lang)
    await context.api.send_message(update=update, view=view)
    return ConversationHandler.END


@HandlersRegistry.register_command(CommandsId.MAIN_MENU, command="main_menu")
async def command_go_to_main_menu(update: Update, context: TMitupContext):
    return await command_start_with_existing_user(update, context)
