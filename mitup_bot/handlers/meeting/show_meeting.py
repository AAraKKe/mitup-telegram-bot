import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.SHOW_MEETING_CALLBACK, callback_data=cb.SHOW_MEETING, bindable=True
)
@with_async_session
async def callback_query_show_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_show_meeting")

    callback_data = guards.valid_callback_data(
        cb.SHOW_MEETING.parse(context.match), MeetingHandlerId.SHOW_MEETING_CALLBACK
    )

    meeting_id = callback_data.id

    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Show meeting",
        update,
        context,
        custom_keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(lang=user.lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                ),
            ]
        ],
    )
    if meeting is None:
        return

    await context.api.edit_message(update=update, view=meeting.main_view)
