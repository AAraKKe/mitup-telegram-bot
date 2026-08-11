import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import Feature
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingEditWhenMessages
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views

from ..enums import EditMeetingHandlerId

log = structlog.get_logger(__name__)

# The `action` facet the clear-times confirmation lines carry.
CLEAR_TIMES_ACTION = "clear_times"


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.WHEN_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_WHEN)
@with_session
async def callback_query_when_entry(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_WHEN.parse(context.match), EditMeetingHandlerId.WHEN_ENTRY_CALLBACK
    ).id
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, meeting_id, "edit_meeting_when", context)

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CLEAR_TIMES_CALLBACK, callback_data=cb.DELETE_MEETING_TIMES
)
@with_session
async def callback_query_clear_times(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.CLEAR_TIMES_CALLBACK
    )
    user = await guards.current_user(update, session)

    await guards.meeting(session, user, callback_data.id, "clear_times", context)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=CLEAR_TIMES_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditWhenMessages.CLEAR_CONFIRMATION.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_TIMES.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_TIMES
)
@with_session(write=True)
async def callback_query_confirm_clear_times(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "confirm_clear_times", context)

    # One tap wipes three fields, and the lock going with them is the direct cause of the
    # `locked_on_start_in_progress` refusals no longer happening. Written before the wipe, which is
    # the last moment the values it overwrites still exist.
    log.info(
        "Meeting times cleared",
        user_id=user.db_id,
        reason="owner_confirmed",
        previous_datetime=meeting.datetime,
        previous_end_datetime=meeting.end_datetime,
        lock_on_start_reset=meeting.lock_on_start,
    )

    meeting.datetime = None
    meeting.end_datetime = None
    meeting.lock_on_start = False

    view = meeting_views.when_view(meeting).with_context(MeetingEditWhenMessages.CLEAR_SUCCESS.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "times_cleared"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_TIMES
)
@with_session
async def callback_query_decline_clear_times(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_clear_times", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=CLEAR_TIMES_ACTION)

    view = meeting_views.when_view(meeting).with_context(MeetingEditWhenMessages.CLEAR_DECLINED.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCK_ON_START_CALLBACK, callback_data=cb.SET_MEETING_LOCK_ON_START
)
@with_session(write=True)
async def callback_query_set_lock_on_start(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_LOCK_ON_START.parse(context.match), EditMeetingHandlerId.LOCK_ON_START_CALLBACK
    ).id
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, meeting_id, "set_lock_on_start", context)

    # The rule that later refuses joins once the meeting is under way; the start time is on the line
    # because whether the rule can ever fire depends on there being one.
    log.info(
        "Meeting lock on start toggled",
        user_id=user.db_id,
        old_value=meeting.lock_on_start,
        new_value=not meeting.lock_on_start,
        meeting_datetime=meeting.datetime,
        reason="owner_toggled",
    )

    meeting.lock_on_start = not meeting.lock_on_start

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
