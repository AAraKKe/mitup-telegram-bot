from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(MeetingHandlerId.REFRESH, callback_data=cb.REFRESH_MEETING)
@with_session
async def callback_query_refresh_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(cb.REFRESH_MEETING.parse(context.match), MeetingHandlerId.REFRESH)

    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Refresh meeting",
        context,
        access=guards.MeetingAccess.OWNER_OR_JOINED,
    )

    await context.api.edit_message(update=update, view=meeting_views.view_for(meeting, user))
