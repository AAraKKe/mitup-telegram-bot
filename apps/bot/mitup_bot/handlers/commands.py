from typing import TYPE_CHECKING

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils.entities import build_datetime_link

from .command_enums import CommandsId
from .registry import HandlersRegistry
from .start_payload import INLINE_KIND, StartPayload, parse_start_payload

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
    return await existing_user_start(session, update, context, parse_start_payload(context.args))


async def existing_user_start(
    session: AsyncSession, update: Update, context: TMitupContext, payload: StartPayload | None = None
) -> ConversationMeetingState | int:
    """Route a `/start` (or `/main_menu`) into the screen its deep-link payload asks for.

    Every branch reads `user.lang` and nothing that traverses the meetups/joined_links collections,
    so the user is loaded without them.
    """
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)

    if payload is not None and payload.kind == INLINE_KIND:
        # Local import to avoid circular dependency at load time: commands.py must be fully
        # loaded before create_meeting.py registers the conversation handler that references
        # CommandsId.START_WITH_EXISTING_USER as an entry point.
        from mitup_bot.handlers.meeting.enums import ConversationMeetingState

        view = views.factory.create_meeting_view(ctx, datetime_link=build_datetime_link())
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
    # No payload: `/main_menu` is not a deep-link target, so it always lands on the main menu.
    return await existing_user_start(session, update, context)
