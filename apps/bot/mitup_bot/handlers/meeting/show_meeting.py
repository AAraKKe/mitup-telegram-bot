import structlog
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

log = structlog.get_logger(__name__)


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
        context,
        access=guards.MeetingAccess.OWNER_OR_JOINED,
        custom_keyboard=[[meeting_list_button(callback_data.source, callback_data.page, user.lang)]],
    )

    # The opening scene of a meeting_id-filtered trace: without it a session reads as a mutation
    # with no approach. `OWNER_OR_JOINED` admits exactly the two roles named here.
    log.info(
        "Meeting detail opened",
        user_id=user.db_id,
        viewer_role="owner" if meeting.is_owned_by(user) else "participant",
        # The enum's values are single characters to fit the 64-byte callback budget; the member
        # name is what a reader can act on.
        source=callback_data.source.name.lower() if callback_data.source is not None else None,
        page=callback_data.page,
    )

    back_button = meeting_detail_back_button(callback_data.source, callback_data.page, user.lang)
    await context.api.edit_message(update=update, view=meeting_views.view_for(meeting, user, back_button=back_button))
