from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import Feature
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId
from .utils import active_meetings_cap_reached


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, callback_data=cb.REACTIVATE_MEETING, bindable=True
)
@with_session
async def callback_query_reactivate_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.REACTIVATE_MEETING.parse(context.match), MeetingHandlerId.REACTIVATE_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    # No lock here: reactivation writes `active` unconditionally without reading any participant or
    # capacity state, and the flush-time UPDATE takes the row lock on its own. A join that grabs the
    # per-meeting lock first sees the committed `active` value either way.
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Reactivate meeting",
        update,
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )
    if meeting is None:
        return

    # Reactivating turns an inactive meeting active again, so it counts against the cap. The meeting
    # being reactivated is inactive and therefore excluded from the count. The back button targets
    # the past-meetings list, where the reactivation buttons live.
    past_meetings_button = ButtonConfig(
        text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
        callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
    )
    if await active_meetings_cap_reached(user, update, context, back_button=past_meetings_button):
        return

    meeting.active = True
    meeting.expiration_time = None
    meeting.expiration_notification_sent = False

    success_message = MeetingLifecycleMessages.REACTIVATE_SUCCESS.get(lang=user.lang)
    await context.api.edit_message(
        update=update,
        view=meeting_views.edit_view(meeting).with_context(success_message),
    )

    context.put_feature_metric(Feature.REACTIVATE_MEETING)
