from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId
from .utils import meeting_detail_back_button, meeting_list_button


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.SHOW_MEETING_CALLBACK, callback_data=cb.SHOW_MEETING, bindable=True
)
@with_session
async def callback_query_show_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_paginated_callback_data(
        cb.SHOW_MEETING.parse(context.match), MeetingHandlerId.SHOW_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Show meeting",
        update,
        context,
        access=guards.MeetingAccess.OWNER_OR_JOINED,
        custom_keyboard=[[meeting_list_button(callback_data.source, callback_data.page, user.lang)]],
    )
    if meeting is None:
        return

    back_button = meeting_detail_back_button(callback_data.source, callback_data.page, user.lang)
    await context.api.edit_message(update=update, view=meeting_views.view_for(meeting, user, back_button=back_button))
