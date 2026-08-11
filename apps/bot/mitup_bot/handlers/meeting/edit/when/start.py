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
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText, build_datetime_link
from mitup_bot.utils.messages import CommonMessages, MeetingEditDateTimeMessages
from mitup_bot.views import meeting as meeting_views

from ..enums import ConversationMeetingState, EditMeetingHandlerId
from ..utils import DateTimeEntityFilter, cleanup_states
from . import rules, screens

# The start half of the When feature. `end.py` is its mirror; a change to one usually belongs in
# both.
#
# Entry points        open the editor from the When screen, or reopen it from a button that
#                     outlived the flow (the upsell reply and the calendar's back button)
# START_EDITOR        [Date] -> START_CALENDAR, [Time] -> START_TIME_PROMPT,
#                     a sent date_time entity -> saved, conversation over,
#                     [When] -> When screen, anything else -> the editor with the error on top
# START_CALENDAR      a month arrow -> stays, a day with no time yet -> saved at 23:59 and
#                     START_TIME_PROMPT, a day with a time already set -> saved and START_EDITOR
# START_TIME_PROMPT   HH:MM -> saved, conversation over; anything else -> the prompt with the
#                     error on top

log = structlog.get_logger(__name__)


async def show_start_editor(context: TMitupContext, update: Update, meeting: Meetup, lang: str):
    """Draw the editor and register the flow's context, which every later step reads the meeting from."""
    context.store_meeting_id(ContextId.EDIT_MEETING_START, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_START,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_START_EDIT.with_id(meeting.db_id),
    )
    today = meeting.owner.now_in_tz().date()
    await context.api.edit_message(update=update, view=screens.start_editor_view(meeting, lang, today))


# --- Opening and leaving the flow ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_START_EDITOR,
    callback_data=cb.OPEN_START_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_open_start_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_callback_data(
        cb.OPEN_START_EDITOR.parse(context.match), EditMeetingHandlerId.OPEN_START_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_start_editor", context)

    await show_start_editor(context, update, meeting, user.lang)
    return ConversationMeetingState.START_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REOPEN_START_EDITOR,
    callback_data=cb.REOPEN_START_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_reopen_start_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    """Open the editor from a button that may have outlived the flow that drew it.

    The beyond-horizon upsell is a message of its own and stays in the chat once the conversation is
    over, so this is an entry point rather than a step: the screen it draws is only answerable with
    the conversation live behind it. Taken mid-flow it is the calendar's back button, which reentry
    resolves to the same place.
    """
    callback_data = guards.valid_callback_data(
        cb.REOPEN_START_EDITOR.parse(context.match), EditMeetingHandlerId.REOPEN_START_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "reopen_start_editor", context)

    await show_start_editor(context, update, meeting, user.lang)
    return ConversationMeetingState.START_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CANCEL_START_EDIT,
    callback_data=cb.CANCEL_START_EDIT,
    bindable=False,
)
@with_session
async def callback_query_cancel_start_edit(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    callback_data = guards.valid_callback_data(
        cb.CANCEL_START_EDIT.parse(context.match), EditMeetingHandlerId.CANCEL_START_EDIT
    )
    user = await guards.current_user(update, session)
    # The flow is over either way, so its state is dropped before the guard can abort the handler.
    cleanup_states(context)

    meeting = await guards.meeting(session, user, callback_data.id, "cancel_start_edit", context)

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))
    return ConversationHandler.END


# --- The calendar ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.NAVIGATE_START_CALENDAR,
    callback_data=cb.NAVIGATE_START_CALENDAR,
)
@with_session
async def callback_query_navigate_start_calendar(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_date_callback_data(
        cb.NAVIGATE_START_CALENDAR.parse(context.match), EditMeetingHandlerId.NAVIGATE_START_CALENDAR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "navigate_start_calendar", context)

    now_in_user_timezone = meeting.owner.now_in_tz()
    today_in_user_timezone = now_in_user_timezone.date()
    anchor_date = rules.safe_anchor_date(meeting.datetime, now_in_user_timezone)
    current_date = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    meeting_date_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else None
    # The domain's only deliberately instrumented date arithmetic, and the one line carrying all
    # four dates side by side. Every calendar-boundary report is about a specific meeting in a
    # specific timezone, so it has to survive production's INFO threshold to be of any use.
    log.info(
        "Rendering calendar view",
        user_id=user.db_id,
        timezone=str(meeting.timezone),
        anchor_date=anchor_date,
        current_date=current_date,
        meeting_date=meeting_date_in_tz,
        owner_now=now_in_user_timezone,
        callback_date=callback_data.date,
    )

    await context.api.edit_message(
        update=update,
        view=screens.start_calendar_view(
            guards.render_context(user, update, context),
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.datetime is None,
        ),
    )
    return ConversationMeetingState.START_CALENDAR


async def reject_start_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, start_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when start_dt is beyond their scheduling horizon.

    The tapped calendar message is edited into the rejection carrying the Collaborate button and a
    button back to the calendar, so the upsell replaces the screen instead of stacking an alert on
    top of it; the conversation stays in START_CALENDAR, where the calendar button is handled.
    """
    rejection = rules.start_beyond_horizon(meeting, start_dt)
    if rejection is None:
        return False
    today = meeting.owner.now_in_tz().date()
    view = screens.start_horizon_calendar_view(rejection, meeting.owner.lang, meeting.db_id, today)
    await context.api.edit_message(update=update, view=view)
    return True


async def set_first_start_date(
    context: TMitupContext, update: Update, meeting: Meetup, picked_date: dt.date
) -> ConversationMeetingState:
    """Save a first start date at 23:59 local and ask for the time that replaces the default."""
    proposed_start = dt.datetime.combine(picked_date, dt.time(23, 59, tzinfo=meeting.timezone)).astimezone(dt.UTC)
    if error := rules.validate_start_datetime(proposed_start, meeting, meeting.lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.START_CALENDAR
    if await reject_start_beyond_horizon(context, update, meeting, proposed_start):
        return ConversationMeetingState.START_CALENDAR

    lang = meeting.lang
    end_cleared = rules.apply_start_datetime(meeting, proposed_start, input_source="calendar_first_pick")
    if end_cleared:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=lang),
            show_alert=True,
        )

    context.store_meeting_id(ContextId.EDIT_MEETING_START, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_START,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_START_EDIT.with_id(meeting.db_id),
    )

    await context.api.edit_message(
        update=update, view=screens.start_date_added_view(meeting, lang, end_cleared=end_cleared)
    )
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return ConversationMeetingState.START_TIME_PROMPT


async def update_start_date(
    context: TMitupContext, update: Update, meeting: Meetup, picked_date: dt.date
) -> ConversationMeetingState:
    """Move an existing start onto another day, keeping the local wall-clock time it already had."""
    local_time = rules.to_utc(cast(dt.datetime, meeting.datetime)).astimezone(meeting.timezone).time()
    proposed_start = dt.datetime.combine(picked_date, local_time, tzinfo=meeting.timezone).astimezone(dt.UTC)
    if error := rules.validate_start_datetime(proposed_start, meeting, meeting.lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.START_CALENDAR
    if await reject_start_beyond_horizon(context, update, meeting, proposed_start):
        return ConversationMeetingState.START_CALENDAR

    lang = meeting.lang
    end_cleared = rules.apply_start_datetime(meeting, proposed_start, input_source="calendar_update")
    if end_cleared:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=lang),
            show_alert=True,
        )

    assert meeting.datetime is not None
    context_message = MeetingEditDateTimeMessages.DATE_UPDATED.get(
        lang=lang, datetime=screens.datetime_text(meeting.datetime)
    )
    if end_cleared:
        context_message = screens.prepend_end_cleared_notice(lang=lang, base_message=context_message)

    today = meeting.owner.now_in_tz().date()
    await context.api.edit_message(
        update=update, view=screens.start_editor_view(meeting, lang, today).with_context(context_message)
    )
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return ConversationMeetingState.START_EDITOR


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PICK_START_DATE,
    callback_data=cb.PICK_START_DATE,
)
@with_session(write=True)
async def callback_query_pick_start_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_date_callback_data(
        cb.PICK_START_DATE.parse(context.match), EditMeetingHandlerId.PICK_START_DATE
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "pick_start_date", context)

    if meeting.datetime is None:
        return await set_first_start_date(context, update, meeting, callback_data.date)
    return await update_start_date(context, update, meeting, callback_data.date)


# --- The time prompt ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_START_TIME_PROMPT,
    callback_data=cb.OPEN_START_TIME_PROMPT,
    bindable=False,
)
@with_session
async def callback_query_open_start_time_prompt(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.OPEN_START_TIME_PROMPT.parse(context.match), EditMeetingHandlerId.OPEN_START_TIME_PROMPT
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_start_time_prompt", context)

    lang = meeting.lang
    context.store_meeting_id(ContextId.EDIT_MEETING_START, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_START,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_START_EDIT.with_id(meeting.db_id),
    )
    await context.api.edit_message(update=update, view=screens.start_time_prompt_view(meeting, lang))
    return ConversationMeetingState.START_TIME_PROMPT


# --- Typed input ---


async def save_start_datetime_and_finish(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    start_dt: dt.datetime,
    confirmation: FormattedText,
    *,
    lang: str,
    input_source: str,
) -> int:
    """Shared tail of both typed-start flows; broadcast runs post-commit via write mode.

    ``confirmation`` differs per flow — a typed datetime confirms the date, a typed time confirms
    the time — and the end-cleared notice goes above whichever it is.
    """
    if rules.apply_start_datetime(meeting, start_dt, input_source=input_source):
        confirmation = screens.prepend_end_cleared_notice(lang=lang, base_message=confirmation)

    await context.api.send_message(update=update, view=meeting_views.when_view(meeting).with_context(confirmation))
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    cleanup_states(context)
    return ConversationHandler.END


async def reply_start_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, start_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when start_dt is beyond their scheduling horizon.

    For the message path (a sent datetime entity): the upsell is sent as a reply carrying the
    Collaborate button and a button back to the editor. The caller returns START_EDITOR, where that
    back button is handled. It is checked apart from the other validations so it can carry the
    Collaborate button at all; a raised horizon is exactly what Collaborate offers.
    """
    rejection = rules.start_beyond_horizon(meeting, start_dt)
    if rejection is None:
        return False
    view = screens.start_horizon_reply_view(rejection, meeting.owner.lang, meeting.db_id)
    await context.api.send_message(update=update, view=view)
    return True


@HandlersRegistry.register_message(
    EditMeetingHandlerId.TYPE_START_DATETIME,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_session(write=True)
async def type_start_datetime_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    message = guards.message(update)
    date_entity = next(e for e in (message.entities or []) if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_START, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "type_start_datetime", context)
        lang = user.lang

        if error := rules.validate_start_datetime(unix_time, meeting, lang):
            today = meeting.owner.now_in_tz().date()
            await context.api.send_message(
                update=update, view=screens.start_editor_view(meeting, lang, today, error=error)
            )
            return ConversationMeetingState.START_EDITOR

        if await reply_start_beyond_horizon(context, update, meeting, unix_time):
            return ConversationMeetingState.START_EDITOR

        confirmation = MeetingEditDateTimeMessages.DATE_UPDATED.get(
            lang=lang, datetime=screens.datetime_text(unix_time)
        )
        return await save_start_datetime_and_finish(
            context, update, meeting, unix_time, confirmation, lang=lang, input_source="datetime_entity"
        )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.TYPE_START_TIME,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_session(write=True)
async def type_start_time_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    time_info = cast(Match, context.match).groupdict()

    with context.meeting_id(ContextId.EDIT_MEETING_START, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "type_start_time", context)
        lang = user.lang

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            await context.api.send_message(
                update=update,
                view=screens.start_time_prompt_view(
                    meeting, lang, error=CommonMessages.TIME_INVALID_VALUE.get(lang=lang)
                ),
            )
            log.info(
                "Meeting datetime input rejected",
                user_id=user.db_id,
                reason="invalid_time_value",
                field="start",
                conversation_state=ConversationMeetingState.START_TIME_PROMPT.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "invalid_time"}
            )
            return ConversationMeetingState.START_TIME_PROMPT

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=user.settings.tz)
        date_to_set = user.datetime_in_tz(meeting.datetime or dt.datetime.now(dt.UTC)).date()
        proposed_start = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        if error := rules.validate_start_datetime(proposed_start, meeting, lang):
            await context.api.send_message(
                update=update, view=screens.start_time_prompt_view(meeting, lang, error=error)
            )
            return ConversationMeetingState.START_TIME_PROMPT

        confirmation = MeetingEditDateTimeMessages.TIME_SUCCESS.get(
            lang=lang, datetime=screens.datetime_text(proposed_start)
        )
        return await save_start_datetime_and_finish(
            context, update, meeting, proposed_start, confirmation, lang=lang, input_source="time_message"
        )


# --- Input the screen cannot use ---


@HandlersRegistry.register_message(
    EditMeetingHandlerId.REJECT_START_DATETIME,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def reject_start_datetime_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_START, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "reject_start_datetime", context)

        today = meeting.owner.now_in_tz().date()
        error = CommonMessages.DATETIME_INVALID.get(lang=user.lang, datetime_link=build_datetime_link())
        await context.api.send_message(
            update=update, view=screens.start_editor_view(meeting, user.lang, today, error=error)
        )
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_datetime_format",
            field="start",
            conversation_state=ConversationMeetingState.START_EDITOR.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_datetime_format"}
        )
        return ConversationMeetingState.START_EDITOR


@HandlersRegistry.register_message(
    EditMeetingHandlerId.REJECT_START_TIME,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def reject_start_time_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_START, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "reject_start_time", context)

        await context.api.send_message(
            update=update,
            view=screens.start_time_prompt_view(
                meeting, user.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=user.lang)
            ),
        )
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_time_format",
            field="start",
            conversation_state=ConversationMeetingState.START_TIME_PROMPT.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_time_format"}
        )
        return ConversationMeetingState.START_TIME_PROMPT


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.START_EDITOR_CONVERSATION,
    entry_points_handler_names=[
        EditMeetingHandlerId.OPEN_START_EDITOR,
        # The editor is also an entry point because the buttons that open it outlive the flow: the
        # beyond-horizon upsell is a separate message that stays in the chat once the conversation
        # is over. Reentry is allowed, so this also serves the calendar's back button — PTB matches
        # entry points ahead of the current state's handlers.
        EditMeetingHandlerId.REOPEN_START_EDITOR,
    ],
    states={
        ConversationMeetingState.START_EDITOR: [
            EditMeetingHandlerId.NAVIGATE_START_CALENDAR,
            EditMeetingHandlerId.OPEN_START_TIME_PROMPT,
            EditMeetingHandlerId.CANCEL_START_EDIT,
            EditMeetingHandlerId.TYPE_START_DATETIME,
            EditMeetingHandlerId.REJECT_START_DATETIME,
        ],
        ConversationMeetingState.START_CALENDAR: [
            EditMeetingHandlerId.NAVIGATE_START_CALENDAR,
            EditMeetingHandlerId.PICK_START_DATE,
        ],
        ConversationMeetingState.START_TIME_PROMPT: [
            EditMeetingHandlerId.TYPE_START_TIME,
            EditMeetingHandlerId.REJECT_START_TIME,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL_START_EDIT],
)
