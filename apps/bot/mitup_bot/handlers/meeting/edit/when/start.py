import datetime as dt
from re import Match
from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.datetimes import in_timezone, local_to_utc
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, MeetingEditDateTimeMessages
from mitup_bot.utils.rich_message import RichContent, datetime_link_content
from mitup_bot.views import meeting as meeting_views

from ..enums import ConversationMeetingState, EditMeetingHandlerId
from ..utils import DateTimeEntityFilter, cleanup_states
from . import rules, screens

# The start half of the When feature. `end.py` is its mirror; a change to one usually belongs in
# both.
#
# The whole schedule lives on one screen, the datetime card: the calendar and the time row edit
# in place through their buttons, and the card's single conversation state exists to receive
# typed input: a full datetime entity, or HH:MM once a date is set. Every button that redraws
# the card is an entry point, so a tap on any card, however old, revives the typed-input state.

log = structlog.get_logger(__name__)


def card_month(meeting: Meetup) -> dt.date:
    """The month the card opens on: the scheduled day's month, or the current one."""
    return rules.safe_anchor_date(meeting.datetime, meeting.owner.now_in_tz())


async def show_start_card(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    lang: str,
    *,
    month: dt.date | None = None,
    error: RichContent | None = None,
    as_reply: bool = False,
) -> ConversationMeetingState:
    """Draw the card and register the flow's context, which the typed-input handlers read the
    meeting from. ``as_reply`` sends a fresh card instead of editing, for the paths answering a
    typed message, where the bot's screen must land below the user's text."""
    context.store_meeting_id(ContextId.EDIT_MEETING_START, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_START,
        MeetingEditDateTimeMessages.ON_EXIT,
        cb.CANCEL_START_EDIT.with_id(meeting.db_id),
        lang=lang,
    )
    view = screens.start_datetime_card(meeting, lang, month=month or card_month(meeting), error=error)
    if as_reply:
        await context.api.send_message(update=update, view=view)
    else:
        await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.START_DATETIME_CARD


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

    return await show_start_card(context, update, meeting, user.lang)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REOPEN_START_EDITOR,
    callback_data=cb.REOPEN_START_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_reopen_start_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    """Open the card from a button that may have outlived the flow that drew it.

    The beyond-horizon upsell is a message of its own and stays in the chat once the conversation
    is over, so its back button needs an entry point rather than a step.
    """
    callback_data = guards.valid_callback_data(
        cb.REOPEN_START_EDITOR.parse(context.match), EditMeetingHandlerId.REOPEN_START_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "reopen_start_editor", context)

    return await show_start_card(context, update, meeting, user.lang)


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

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    return ConversationHandler.END


# --- The calendar ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.NAVIGATE_START_CALENDAR,
    callback_data=cb.NAVIGATE_START_CALENDAR,
    bindable=False,
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
    month = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    meeting_date_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else None
    # The domain's only deliberately instrumented date arithmetic, and the one line carrying the
    # dates side by side. Every calendar-boundary report is about a specific meeting in a
    # specific timezone, so it has to survive production's INFO threshold to be of any use.
    log.info(
        "Rendering calendar view",
        user_id=user.db_id,
        timezone=str(meeting.timezone),
        current_date=month,
        meeting_date=meeting_date_in_tz,
        owner_now=now_in_user_timezone,
        callback_date=callback_data.date,
    )

    return await show_start_card(context, update, meeting, user.lang, month=month)


async def reject_start_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, start_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when start_dt is beyond their scheduling horizon.

    The tapped card is edited into the rejection carrying the Collaborate button and a button
    back to the card, so the upsell replaces the screen instead of stacking an alert on top of it.
    """
    rejection = rules.start_beyond_horizon(meeting, start_dt)
    if rejection is None:
        return False
    today = meeting.owner.now_in_tz().date()
    view = screens.start_horizon_calendar_view(rejection, meeting.owner.lang, meeting.db_id, today)
    await context.api.edit_message(update=update, view=view)
    return True


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PICK_START_DATE,
    callback_data=cb.PICK_START_DATE,
    bindable=False,
)
@with_session(write=True)
async def callback_query_pick_start_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    """Set or move the start onto the tapped day, and redraw the card showing it.

    A first pick lands at 23:59 local, the value the time row then offers to replace; a later
    pick keeps the wall-clock time the meeting already had.
    """
    callback_data = guards.valid_date_callback_data(
        cb.PICK_START_DATE.parse(context.match), EditMeetingHandlerId.PICK_START_DATE
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "pick_start_date", context)
    lang = meeting.lang

    if meeting.datetime is None:
        proposed_start = local_to_utc(callback_data.date, dt.time(23, 59), meeting.timezone)
        input_source = "calendar_first_pick"
    else:
        local_time = in_timezone(meeting.datetime, meeting.timezone).time()
        proposed_start = local_to_utc(callback_data.date, local_time, meeting.timezone)
        input_source = "calendar_update"

    if error := rules.validate_start_datetime(proposed_start, meeting, lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.START_DATETIME_CARD
    if await reject_start_beyond_horizon(context, update, meeting, proposed_start):
        return ConversationMeetingState.START_DATETIME_CARD

    end_cleared = rules.apply_start_datetime(meeting, proposed_start, input_source=input_source)
    if end_cleared:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.text(lang=lang),
            show_alert=True,
        )

    state = await show_start_card(context, update, meeting, lang, month=callback_data.date)
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return state


# --- The time row ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_START_TIME_PROMPT,
    callback_data=cb.OPEN_START_TIME_PROMPT,
    bindable=False,
)
@with_session
async def callback_query_open_start_time_prompt(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    """The time row's Edit chip: replace the card with the typed-time prompt.

    The typed answer is a new message, so it earns a fresh card below it; the prompt's Cancel
    redraws the card in place instead.
    """
    callback_data = guards.valid_callback_data(
        cb.OPEN_START_TIME_PROMPT.parse(context.match), EditMeetingHandlerId.OPEN_START_TIME_PROMPT
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_start_time_prompt", context)
    lang = meeting.lang

    context.store_meeting_id(ContextId.EDIT_MEETING_START, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_START,
        MeetingEditDateTimeMessages.ON_EXIT,
        cb.CANCEL_START_EDIT.with_id(meeting.db_id),
        lang=lang,
    )
    await context.api.edit_message(
        update=update, view=screens.time_prompt_view(lang, cb.REOPEN_START_EDITOR.with_id(meeting.db_id))
    )
    return ConversationMeetingState.START_DATETIME_CARD


# --- Typed input ---


async def save_start_datetime(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    start_dt: dt.datetime,
    *,
    lang: str,
    input_source: str,
) -> ConversationMeetingState:
    """Shared tail of both typed-start paths: apply, answer with a fresh card, fan out.

    The card showing the new moment is the feedback; only the end time getting cleared along the
    way is a side effect the card does not explain, so only that gets a banner.
    """
    end_cleared = rules.apply_start_datetime(meeting, start_dt, input_source=input_source)
    error = MeetingEditDateTimeMessages.END_CLEARED_BY_START.rich(lang=lang) if end_cleared else None
    state = await show_start_card(context, update, meeting, lang, error=error, as_reply=True)

    await context.api.update_meeting_messages(meeting=meeting)
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return state


async def reply_start_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, start_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when start_dt is beyond their scheduling horizon.

    For the message path (a sent datetime entity): the upsell is sent as a reply carrying the
    Collaborate button and a button back to the card. It is checked apart from the other
    validations so it can carry the Collaborate button at all; a raised horizon is exactly what
    Collaborate offers.
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
            return await show_start_card(context, update, meeting, lang, error=RichContent(error), as_reply=True)

        if await reply_start_beyond_horizon(context, update, meeting, unix_time):
            return ConversationMeetingState.START_DATETIME_CARD

        return await save_start_datetime(context, update, meeting, unix_time, lang=lang, input_source="datetime_entity")


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

        if meeting.datetime is None:
            state = await show_start_card(
                context,
                update,
                meeting,
                lang,
                error=MeetingEditDateTimeMessages.TIME_NEEDS_DATE.rich(lang=lang),
                as_reply=True,
            )
            log.info(
                "Meeting datetime input rejected",
                user_id=user.db_id,
                reason="time_without_date",
                field="start",
                conversation_state=ConversationMeetingState.START_DATETIME_CARD.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "time_without_date"}
            )
            return state

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            state = await show_start_card(
                context, update, meeting, lang, error=CommonMessages.TIME_INVALID_VALUE.rich(lang=lang), as_reply=True
            )
            log.info(
                "Meeting datetime input rejected",
                user_id=user.db_id,
                reason="invalid_time_value",
                field="start",
                conversation_state=ConversationMeetingState.START_DATETIME_CARD.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "invalid_time"}
            )
            return state

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]))
        date_to_set = user.datetime_in_tz(meeting.datetime).date()
        proposed_start = local_to_utc(date_to_set, user_time, user.settings.tz)

        if error := rules.validate_start_datetime(proposed_start, meeting, lang):
            return await show_start_card(context, update, meeting, lang, error=RichContent(error), as_reply=True)

        return await save_start_datetime(
            context, update, meeting, proposed_start, lang=lang, input_source="time_message"
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

        error = CommonMessages.DATETIME_INPUT_INVALID.rich(lang=user.lang, datetime_link=datetime_link_content())
        state = await show_start_card(context, update, meeting, user.lang, error=error, as_reply=True)
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_datetime_format",
            field="start",
            conversation_state=ConversationMeetingState.START_DATETIME_CARD.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_datetime_format"}
        )
        return state


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.START_EDITOR_CONVERSATION,
    entry_points_handler_names=[
        EditMeetingHandlerId.OPEN_START_EDITOR,
        # Every button that redraws the card is also an entry point: the card outlives the
        # conversation (the upsell reply stays in the chat, and any old card can be tapped), and
        # reviving the conversation on any tap is what keeps typed input working afterwards. PTB
        # matches entry points ahead of the current state's handlers.
        EditMeetingHandlerId.REOPEN_START_EDITOR,
        EditMeetingHandlerId.NAVIGATE_START_CALENDAR,
        EditMeetingHandlerId.PICK_START_DATE,
        EditMeetingHandlerId.OPEN_START_TIME_PROMPT,
    ],
    states={
        ConversationMeetingState.START_DATETIME_CARD: [
            EditMeetingHandlerId.TYPE_START_DATETIME,
            EditMeetingHandlerId.TYPE_START_TIME,
            EditMeetingHandlerId.CANCEL_START_EDIT,
            EditMeetingHandlerId.REJECT_START_DATETIME,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL_START_EDIT],
)
