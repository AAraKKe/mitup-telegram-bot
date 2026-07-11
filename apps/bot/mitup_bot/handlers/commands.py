from typing import TYPE_CHECKING

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext

from .command_enums import CommandsId
from .registry import HandlersRegistry

if TYPE_CHECKING:
    # Runtime import would be circular (the meeting package pulls in create_meeting.py,
    # which needs commands.py fully loaded); see the local import in existing_user_start.
    from mitup_bot.handlers.meeting.enums import ConversationMeetingState


@HandlersRegistry.register_command(
    CommandsId.START_WITH_EXISTING_USER,
    command="start",
)
@with_session
async def command_start_with_existing_user(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    if await guards.member_user(update, session) is None:
        # Not a MEMBER: the re-onboarding conversation (bound in REGISTRATION_HANDLERS_GROUP,
        # which runs before this group) claims those /start updates with ApplicationHandlerStop,
        # so this branch only runs if that routing ever breaks. Stay silent either way.
        return ConversationHandler.END
    return await existing_user_start(session, update, context)


async def existing_user_start(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    # Entry path only reads `user.lang` for the create-meeting / main-menu views; it never traverses
    # the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session, load_collections=False)
    ctx = guards.render_context(user, update, context)

    if context.args and context.args[0] == "inline":
        # Local import to avoid circular dependency at load time: commands.py must be fully
        # loaded before create_meeting.py registers the conversation handler that references
        # CommandsId.START_WITH_EXISTING_USER as an entry point.
        from mitup_bot.handlers.meeting.enums import ConversationMeetingState

        view = views.factory.create_meeting_view(ctx)
        await context.api.send_message(update=update, view=view)
        return ConversationMeetingState.TITLE

    view = views.factory.main_menu_view(ctx)
    await context.api.send_message(update=update, view=view)
    return ConversationHandler.END


@HandlersRegistry.register_command(CommandsId.MAIN_MENU, command="main_menu")
@with_session
async def command_go_to_main_menu(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    return await existing_user_start(session, update, context)
