import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import Feature
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, MeetingEditWhenMessages
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views

from ..enums import EditMeetingHandlerId
from ..utils import cleanup_states, log_stale_navigation

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

    # No current screen renders this button; it survives only on old messages, so the tap lands
    # on the editor card, which owns the start and end rows.
    log_stale_navigation(user, "edit_meeting_when")
    await context.api.edit_message(
        update=update,
        view=meeting_views.owner_view(meeting).with_context(CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user.lang)),
    )


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

    # The confirmation leaves the card flow, so the flow's typed-input context goes with it.
    cleanup_states(context)
    log.info("Meeting destructive action prompted", user_id=user.db_id, action=CLEAR_TIMES_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditWhenMessages.REMOVE_TIMES_CONFIRMATION.rich(lang=user.lang),
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

    # Written before the wipe, which is the last moment the values it overwrites still exist.
    # `lock_on_start` is a standing setting and survives untouched: it just has no window to
    # freeze until a new start time is set.
    log.info(
        "Meeting times cleared",
        user_id=user.db_id,
        reason="owner_confirmed",
        previous_datetime=meeting.datetime,
        previous_end_datetime=meeting.end_datetime,
        lock_on_start=meeting.lock_on_start,
    )

    meeting.datetime = None
    meeting.end_datetime = None

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
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

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
