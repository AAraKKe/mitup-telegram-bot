import datetime as dt

import structlog
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

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, callback_data=cb.REACTIVATE_MEETING, bindable=True
)
@with_session
async def callback_query_reactivate_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.REACTIVATE_MEETING.parse(context.match), MeetingHandlerId.REACTIVATE_MEETING_CALLBACK
    )

    # load_collections: the cap check below counts the user's active meetings off `user.meetups`.
    user = await guards.current_user(update, session, load_collections=True)

    # No lock here: reactivation writes `active` unconditionally without reading any participant or
    # capacity state, and the flush-time UPDATE takes the row lock on its own. A join that grabs the
    # per-meeting lock first sees the committed `active` value either way.
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Reactivate meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    # A live meeting is left exactly as it is: the restart below clears its dates, and the button can
    # arrive from a past-meetings list or a deletion warning the owner already acted on, so a second
    # tap must not wipe the date they set after the first one.
    if meeting.active:
        log.info(
            "Skip meeting reactivation",
            meeting_id=meeting.db_id,
            tg_user_id=user.tg_user_id,
            reason="already_active",
        )
        await context.api.edit_message(update=update, view=meeting_views.edit_view(meeting))
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

    # Between them the writes below cancel a pending deletion: they reset the retention clock,
    # discard the record that the owner was warned, and restart the dateless window. Recorded
    # before the mutation, which is the last moment the values they overwrite still exist.
    log.info(
        "Meeting reactivated",
        meeting_id=meeting.db_id,
        tg_user_id=user.tg_user_id,
        previous_activated_time=meeting.activated_time,
        previous_expiration_time=meeting.expiration_time,
        was_warned=meeting.expiration_notification_sent,
        reason="owner_reactivated",
    )

    # A reactivation is a restart, not a resume: the meeting comes back as a fresh draft with its
    # dates cleared, so the owner picks a new date instead of inheriting the one that already passed.
    # The re-stamped `activated_time` gives the draft the full dateless window again, and the
    # deactivation sweep judges it from there. Deactivation already dropped the participants and
    # message cards, so there is nothing else to reset.
    meeting.active = True
    meeting.activated_time = dt.datetime.now(dt.UTC)
    meeting.datetime = None
    meeting.end_datetime = None
    # `lock_on_start` is a rule about a start time; it goes with the dates, as it does when the owner
    # clears the times by hand.
    meeting.lock_on_start = False
    meeting.expiration_time = None
    meeting.expiration_notification_sent = False

    success_message = MeetingLifecycleMessages.REACTIVATE_SUCCESS.get(lang=user.lang)
    await context.api.edit_message(
        update=update,
        view=meeting_views.edit_view(meeting).with_context(success_message),
    )

    context.put_feature_metric(Feature.REACTIVATE_MEETING)
