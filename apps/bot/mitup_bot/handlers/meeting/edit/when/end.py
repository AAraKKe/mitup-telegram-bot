import datetime as dt
from re import Match
from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import build_datetime_link
from mitup_bot.utils.messages import CommonMessages, MeetingEditDurationMessages
from mitup_bot.views import meeting as meeting_views

from ..enums import ConversationMeetingState, EditMeetingHandlerId
from ..utils import DateTimeEntityFilter, cleanup_states
from . import rules, screens

# The end half of the When feature. `start.py` is its mirror; a change to one usually belongs in
# both. Everything here needs the meeting to already have a start time, since an end is a span
# measured from one.
#
# Entry points        open the editor from the When screen, or reopen it from a button that
#                     outlived the flow (the upsell reply and the calendar's back button)
# END_EDITOR          [Date] -> END_CALENDAR, [Time] -> END_TIME_PROMPT,
#                     a sent date_time entity -> saved, conversation over,
#                     [When] -> When screen, anything else -> the editor with the error on top
# END_CALENDAR        a month arrow -> stays, a day with no time yet -> saved at 23:59 and
#                     END_TIME_PROMPT, a day with a time already set -> saved and END_EDITOR
# END_TIME_PROMPT     HH:MM or a sent date_time entity -> saved, conversation over;
#                     anything else -> the prompt with the error on top

log = structlog.get_logger(__name__)


async def show_end_editor(context: TMitupContext, update: Update, meeting: Meetup, lang: str):
    """Draw the editor and register the flow's context, which every later step reads the meeting from."""
    context.store_meeting_id(ContextId.EDIT_MEETING_END, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_END,
        MeetingEditDurationMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_END_EDIT.with_id(meeting.db_id),
    )
    await context.api.edit_message(update=update, view=screens.end_editor_view(meeting, lang))


async def refuse_without_start_time(context: TMitupContext, update: Update, user: User, meeting: Meetup) -> bool:
    """Return True, having told the owner why, when the meeting has no start time to end from.

    Every button that opens this flow was drawn while the meeting had a start time, and the owner can
    clear it in between — from the When screen, or by reactivating the meeting. Both entry points ask
    this before showing a screen whose whole subject is a span measured from that start.
    """
    if meeting.datetime is not None:
        return False

    log.info("Meeting duration edit refused", user_id=user.db_id, reason="start_datetime_missing")
    await context.api.answer_callback_query(
        update,
        text=MeetingEditDurationMessages.END_STALE_ALERT.get_text(lang=user.lang),
        show_alert=True,
    )
    return True


# --- Opening and leaving the flow ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_END_EDITOR,
    callback_data=cb.OPEN_END_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_open_end_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_callback_data(
        cb.OPEN_END_EDITOR.parse(context.match), EditMeetingHandlerId.OPEN_END_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_end_editor", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    await show_end_editor(context, update, meeting, user.lang)
    return ConversationMeetingState.END_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REOPEN_END_EDITOR,
    callback_data=cb.REOPEN_END_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_reopen_end_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    """Open the editor from a button that may have outlived the flow that drew it.

    The beyond-horizon upsell is a message of its own and stays in the chat once the conversation is
    over, so this is an entry point rather than a step: the screen it draws is only answerable with
    the conversation live behind it. Taken mid-flow it is the calendar's back button, which reentry
    resolves to the same place.
    """
    callback_data = guards.valid_callback_data(
        cb.REOPEN_END_EDITOR.parse(context.match), EditMeetingHandlerId.REOPEN_END_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "reopen_end_editor", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    await show_end_editor(context, update, meeting, user.lang)
    return ConversationMeetingState.END_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CANCEL_END_EDIT,
    callback_data=cb.CANCEL_END_EDIT,
    bindable=False,
)
@with_session
async def callback_query_cancel_end_edit(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    context.clean_all_user_data(reason="end_edit_cancelled")

    callback_data = guards.valid_callback_data(
        cb.CANCEL_END_EDIT.parse(context.match), EditMeetingHandlerId.CANCEL_END_EDIT
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "cancel_end_edit", context)

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))
    return ConversationHandler.END


# --- The calendar ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.NAVIGATE_END_CALENDAR,
    callback_data=cb.NAVIGATE_END_CALENDAR,
    bindable=False,
)
@with_session
async def callback_query_navigate_end_calendar(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    callback_data = guards.valid_date_callback_data(
        cb.NAVIGATE_END_CALENDAR.parse(context.match), EditMeetingHandlerId.NAVIGATE_END_CALENDAR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "navigate_end_calendar", context)

    now_in_user_timezone = meeting.owner.now_in_tz()
    today_in_user_timezone = now_in_user_timezone.date()
    anchor_date = rules.safe_anchor_date(meeting.end_datetime, now_in_user_timezone)
    current_date = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    await context.api.edit_message(
        update=update,
        view=screens.end_calendar_view(
            guards.render_context(user, update, context),
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.end_datetime is None,
        ),
    )
    return ConversationMeetingState.END_CALENDAR


async def reject_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    The tapped calendar message is edited into the rejection carrying the Collaborate button and a
    button back to the calendar, so the upsell replaces the screen instead of stacking an alert on
    top of it; the conversation stays in END_CALENDAR, where the calendar button is handled.
    """
    rejection = rules.end_beyond_horizon(meeting, end_dt)
    if rejection is None:
        return False
    today = meeting.owner.now_in_tz().date()
    view = screens.end_horizon_calendar_view(rejection, meeting.owner.lang, meeting.db_id, today)
    await context.api.edit_message(update=update, view=view)
    return True


async def set_first_end_date(
    context: TMitupContext, update: Update, meeting: Meetup, picked_date: dt.date, lang: str
) -> ConversationMeetingState:
    """Save a first end date at 23:59 local and ask for the time that replaces the default."""
    proposed_end = dt.datetime.combine(picked_date, dt.time(23, 59, tzinfo=meeting.timezone)).astimezone(dt.UTC)
    if error := rules.validate_end_datetime(proposed_end, meeting, lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.END_CALENDAR
    if await reject_end_beyond_horizon(context, update, meeting, proposed_end):
        return ConversationMeetingState.END_CALENDAR

    rules.apply_end_datetime(meeting, proposed_end, input_source="calendar_first_pick")

    await context.api.edit_message(update=update, view=screens.end_date_added_view(meeting, meeting.lang))
    return ConversationMeetingState.END_TIME_PROMPT


async def update_end_date(
    context: TMitupContext, update: Update, meeting: Meetup, picked_date: dt.date, lang: str
) -> ConversationMeetingState:
    """Move an existing end onto another day, keeping the local wall-clock time it already had."""
    assert meeting.end_datetime is not None
    local_end_time = rules.to_utc(meeting.end_datetime).astimezone(meeting.timezone).time()
    proposed_end = dt.datetime.combine(picked_date, local_end_time, tzinfo=meeting.timezone).astimezone(dt.UTC)
    if error := rules.validate_end_datetime(proposed_end, meeting, lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.END_CALENDAR
    if await reject_end_beyond_horizon(context, update, meeting, proposed_end):
        return ConversationMeetingState.END_CALENDAR

    rules.apply_end_datetime(meeting, proposed_end, input_source="calendar_update")

    await show_end_editor(context, update, meeting, lang)
    return ConversationMeetingState.END_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PICK_END_DATE,
    callback_data=cb.PICK_END_DATE,
    bindable=False,
)
@with_session(write=True)
async def callback_query_pick_end_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_date_callback_data(
        cb.PICK_END_DATE.parse(context.match), EditMeetingHandlerId.PICK_END_DATE
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "pick_end_date", context)

    if meeting.end_datetime is None:
        return await set_first_end_date(context, update, meeting, callback_data.date, user.lang)
    return await update_end_date(context, update, meeting, callback_data.date, user.lang)


# --- The time prompt ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_END_TIME_PROMPT,
    callback_data=cb.OPEN_END_TIME_PROMPT,
    bindable=False,
)
@with_session
async def callback_query_open_end_time_prompt(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.OPEN_END_TIME_PROMPT.parse(context.match), EditMeetingHandlerId.OPEN_END_TIME_PROMPT
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_end_time_prompt", context)

    await context.api.edit_message(update=update, view=screens.end_time_prompt_view(meeting, user.lang))
    return ConversationMeetingState.END_TIME_PROMPT


# --- Typed input ---


async def save_end_datetime_and_finish(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    end_dt: dt.datetime,
    *,
    input_source: str,
) -> int:
    """Shared tail of both end-datetime flows; broadcast runs post-commit via write mode."""
    rules.apply_end_datetime(meeting, end_dt, input_source=input_source)

    await context.api.send_message(update=update, view=meeting_views.when_view(meeting))
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "end_datetime"})
    cleanup_states(context)
    return ConversationHandler.END


async def reply_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    For the message paths (a sent datetime entity or an HH:MM time): the upsell is sent as a reply
    carrying the Collaborate button and a button back to the editor. The caller returns END_EDITOR,
    where that back button is handled.
    """
    rejection = rules.end_beyond_horizon(meeting, end_dt)
    if rejection is None:
        return False
    view = screens.end_horizon_reply_view(rejection, meeting.owner.lang, meeting.db_id)
    await context.api.send_message(update=update, view=view)
    return True


@HandlersRegistry.register_message(
    EditMeetingHandlerId.TYPE_END_DATETIME,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_session(write=True)
async def type_end_datetime_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    message = guards.message(update)
    date_entity = next(e for e in (message.entities or []) if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_END, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "type_end_datetime", context)

        if error := rules.validate_end_datetime(unix_time, meeting, user.lang):
            await context.api.send_message(update=update, view=screens.end_editor_view(meeting, user.lang, error=error))
            return ConversationMeetingState.END_EDITOR

        if await reply_end_beyond_horizon(context, update, meeting, unix_time):
            return ConversationMeetingState.END_EDITOR

        return await save_end_datetime_and_finish(context, update, meeting, unix_time, input_source="datetime_entity")


@HandlersRegistry.register_message(
    EditMeetingHandlerId.TYPE_END_TIME,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_session(write=True)
async def type_end_time_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    time_info = cast(Match, context.match).groupdict()

    with context.meeting_id(ContextId.EDIT_MEETING_END, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "type_end_time", context)
        lang = user.lang

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            await context.api.send_message(
                update=update,
                view=screens.end_time_prompt_view(
                    meeting, lang, error=CommonMessages.TIME_INVALID_VALUE.get(lang=lang)
                ),
            )
            log.info(
                "Meeting datetime input rejected",
                user_id=user.db_id,
                reason="invalid_time_value",
                field="end",
                conversation_state=ConversationMeetingState.END_TIME_PROMPT.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "invalid_time"}
            )
            return ConversationMeetingState.END_TIME_PROMPT

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=user.settings.tz)
        date_to_set = user.datetime_in_tz(meeting.end_datetime or dt.datetime.now(dt.UTC)).date()
        proposed_end = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        if error := rules.validate_end_datetime(proposed_end, meeting, lang):
            await context.api.send_message(update=update, view=screens.end_time_prompt_view(meeting, lang, error=error))
            return ConversationMeetingState.END_TIME_PROMPT

        if await reply_end_beyond_horizon(context, update, meeting, proposed_end):
            return ConversationMeetingState.END_EDITOR

        return await save_end_datetime_and_finish(context, update, meeting, proposed_end, input_source="time_message")


# --- Input the screen cannot use ---


@HandlersRegistry.register_message(
    EditMeetingHandlerId.REJECT_END_DATETIME,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def reject_end_datetime_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_END, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "reject_end_datetime", context)

        error = CommonMessages.DATETIME_INVALID.get(lang=user.lang, datetime_link=build_datetime_link())
        await context.api.send_message(update=update, view=screens.end_editor_view(meeting, user.lang, error=error))
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_datetime_format",
            field="end",
            conversation_state=ConversationMeetingState.END_EDITOR.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_end_datetime_format"}
        )
        return ConversationMeetingState.END_EDITOR


@HandlersRegistry.register_message(
    EditMeetingHandlerId.REJECT_END_TIME,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def reject_end_time_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_END, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "reject_end_time", context)

        await context.api.send_message(
            update=update,
            view=screens.end_time_prompt_view(
                meeting, user.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=user.lang)
            ),
        )
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_time_format",
            field="end",
            conversation_state=ConversationMeetingState.END_TIME_PROMPT.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_time_format"}
        )
        return ConversationMeetingState.END_TIME_PROMPT


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.END_EDITOR_CONVERSATION,
    entry_points_handler_names=[
        EditMeetingHandlerId.OPEN_END_EDITOR,
        # The editor is also an entry point because the buttons that open it outlive the flow: the
        # beyond-horizon upsell is a separate message that stays in the chat once the conversation
        # is over. Reentry is allowed, so this also serves the calendar's back button — PTB matches
        # entry points ahead of the current state's handlers.
        EditMeetingHandlerId.REOPEN_END_EDITOR,
    ],
    states={
        ConversationMeetingState.END_EDITOR: [
            EditMeetingHandlerId.NAVIGATE_END_CALENDAR,
            EditMeetingHandlerId.OPEN_END_TIME_PROMPT,
            EditMeetingHandlerId.CANCEL_END_EDIT,
            EditMeetingHandlerId.TYPE_END_DATETIME,
            EditMeetingHandlerId.REJECT_END_DATETIME,
        ],
        ConversationMeetingState.END_CALENDAR: [
            EditMeetingHandlerId.NAVIGATE_END_CALENDAR,
            EditMeetingHandlerId.PICK_END_DATE,
        ],
        ConversationMeetingState.END_TIME_PROMPT: [
            EditMeetingHandlerId.TYPE_END_TIME,
            EditMeetingHandlerId.TYPE_END_DATETIME,
            EditMeetingHandlerId.CANCEL_END_EDIT,
            EditMeetingHandlerId.REJECT_END_TIME,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL],
)
