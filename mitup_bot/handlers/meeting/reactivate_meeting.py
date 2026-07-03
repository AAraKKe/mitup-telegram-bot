from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.models import Meetup
from mitup_bot.utils import MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, callback_data=cb.REACTIVATE_MEETING, bindable=True
)
@with_session
async def callback_query_reactivate_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.REACTIVATE_MEETING.parse(context.match), MeetingHandlerId.REACTIVATE_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Reactivate meeting", update, context)
    if meeting is None:
        return

    full_meeting = await Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    full_meeting.active = True
    full_meeting.expiration_time = None
    full_meeting.expiration_notification_sent = False

    success_message = MeetingLifecycleMessages.REACTIVATE_SUCCESS.get(lang=user.lang)
    await context.api.edit_message(
        update=update,
        view=full_meeting.edit_view.with_context(success_message),
    )
