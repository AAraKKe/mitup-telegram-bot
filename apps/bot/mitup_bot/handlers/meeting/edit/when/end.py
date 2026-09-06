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
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    CommonMessages,
    MeetingEditDateTimeMessages,
    MeetingEditDurationMessages,
    MeetingEditWhenMessages,
)
from mitup_bot.utils.rich_message import RichContent, datetime_link_content
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views

from ..enums import ConversationMeetingState, EditMeetingHandlerId
from ..utils import DateTimeEntityFilter, cleanup_states
from . import rules, screens

# The `action` facet the remove-end-time confirmation lines carry.
REMOVE_END_TIME_ACTION = "remove_end_time"

# The end half of the When feature. `start.py` is its mirror; a change to one usually belongs in
# both. Everything here needs the meeting to already have a start time, since an end is a span
# measured from one.
#
# The whole end schedule lives on one screen, the datetime card: the calendar and the time row
# edit in place through their buttons, and the card's single conversation state exists to receive
# typed input: a full datetime entity, or HH:MM once an end date is set. Every button that
# redraws the card is an entry point, so a tap on any card, however old, revives the typed-input
# state.

log = structlog.get_logger(__name__)


def card_month(meeting: Meetup) -> dt.date:
    """The month the card opens on: the end's month, the start's while no end exists, or the
    current one. Anchoring on the start matters because an end usually lands near it."""
    return rules.safe_anchor_date(meeting.end_datetime or meeting.datetime, meeting.owner.now_in_tz())


async def show_end_card(
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
    context.store_meeting_id(ContextId.EDIT_MEETING_END, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_END,
        MeetingEditDurationMessages.ON_EXIT,
        cb.CANCEL_END_EDIT.with_id(meeting.db_id),
        lang=lang,
    )
    view = screens.end_datetime_card(meeting, lang, month=month or card_month(meeting), error=error)
    if as_reply:
        await context.api.send_message(update=update, view=view)
    else:
        await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.END_DATETIME_CARD


async def refuse_without_start_time(context: TMitupContext, update: Update, user: User, meeting: Meetup) -> bool:
    """Return True, having told the owner why, when the meeting has no start time to end from.

    Every button that opens this flow was drawn while the meeting had a start time, and the owner
    can clear it in between. Every entry point asks this before showing a screen whose whole
    subject is a span measured from that start.
    """
    if meeting.datetime is not None:
        return False

    log.info("Meeting duration edit refused", user_id=user.db_id, reason="start_datetime_missing")
    await context.api.answer_callback_query(
        update,
        text=MeetingEditDurationMessages.END_STALE_ALERT.text(lang=user.lang),
        show_alert=True,
    )
    return True


async def recover_typed_without_start_time(context: TMitupContext, update: Update, user: User, meeting: Meetup) -> int:
    """Answer typed end input on a meeting whose start is gone, and end the flow.

    The typed paths cannot answer with an alert (there is no callback to answer), so the notice
    rides on the meeting editor, the screen that owns setting a start again.
    """
    log.info("Meeting duration edit refused", user_id=user.db_id, reason="start_datetime_missing")
    cleanup_states(context)
    await context.api.send_message(
        update=update,
        view=meeting_views.owner_view(meeting).with_context(
            MeetingEditDurationMessages.END_STALE_ALERT.rich(lang=user.lang)
        ),
    )
    return ConversationHandler.END


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

    return await show_end_card(context, update, meeting, user.lang)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REOPEN_END_EDITOR,
    callback_data=cb.REOPEN_END_EDITOR,
    bindable=False,
)
@with_session
async def callback_query_reopen_end_editor(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    """Open the card from a button that may have outlived the flow that drew it.

    The beyond-horizon upsell is a message of its own and stays in the chat once the conversation
    is over, so its back button needs an entry point rather than a step.
    """
    callback_data = guards.valid_callback_data(
        cb.REOPEN_END_EDITOR.parse(context.match), EditMeetingHandlerId.REOPEN_END_EDITOR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "reopen_end_editor", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    return await show_end_card(context, update, meeting, user.lang)


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

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
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
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_date_callback_data(
        cb.NAVIGATE_END_CALENDAR.parse(context.match), EditMeetingHandlerId.NAVIGATE_END_CALENDAR
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "navigate_end_calendar", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    today_in_user_timezone = meeting.owner.now_in_tz().date()
    month = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    return await show_end_card(context, update, meeting, user.lang, month=month)


async def reject_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    The tapped card is edited into the rejection carrying the Collaborate button and a button
    back to the card, so the upsell replaces the screen instead of stacking an alert on top of it.
    """
    rejection = rules.end_beyond_horizon(meeting, end_dt)
    if rejection is None:
        return False
    today = meeting.owner.now_in_tz().date()
    view = screens.end_horizon_calendar_view(rejection, meeting.owner.lang, meeting.db_id, today)
    await context.api.edit_message(update=update, view=view)
    return True


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PICK_END_DATE,
    callback_data=cb.PICK_END_DATE,
    bindable=False,
)
@with_session(write=True)
async def callback_query_pick_end_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    """Set or move the end onto the tapped day, and redraw the card showing it.

    A first pick lands at 23:59 local, the value the time row then offers to replace; a later
    pick keeps the wall-clock time the end already had.
    """
    callback_data = guards.valid_date_callback_data(
        cb.PICK_END_DATE.parse(context.match), EditMeetingHandlerId.PICK_END_DATE
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "pick_end_date", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    lang = user.lang
    if meeting.end_datetime is None:
        proposed_end = local_to_utc(callback_data.date, dt.time(23, 59), meeting.timezone)
        input_source = "calendar_first_pick"
    else:
        local_end_time = in_timezone(meeting.end_datetime, meeting.timezone).time()
        proposed_end = local_to_utc(callback_data.date, local_end_time, meeting.timezone)
        input_source = "calendar_update"

    if error := rules.validate_end_datetime(proposed_end, meeting, lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.END_DATETIME_CARD
    if await reject_end_beyond_horizon(context, update, meeting, proposed_end):
        return ConversationMeetingState.END_DATETIME_CARD

    rules.apply_end_datetime(meeting, proposed_end, input_source=input_source)

    state = await show_end_card(context, update, meeting, lang, month=callback_data.date)
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "end_datetime"})
    return state


# --- The time row ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_END_TIME_PROMPT,
    callback_data=cb.OPEN_END_TIME_PROMPT,
    bindable=False,
)
@with_session
async def callback_query_open_end_time_prompt(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    """The time row's Edit chip: replace the card with the typed-time prompt.

    The typed answer is a new message, so it earns a fresh card below it; the prompt's Cancel
    redraws the card in place instead.
    """
    callback_data = guards.valid_callback_data(
        cb.OPEN_END_TIME_PROMPT.parse(context.match), EditMeetingHandlerId.OPEN_END_TIME_PROMPT
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "open_end_time_prompt", context)

    if await refuse_without_start_time(context, update, user, meeting):
        return ConversationHandler.END

    lang = meeting.lang
    context.store_meeting_id(ContextId.EDIT_MEETING_END, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_END,
        MeetingEditDurationMessages.ON_EXIT,
        cb.CANCEL_END_EDIT.with_id(meeting.db_id),
        lang=lang,
    )
    await context.api.edit_message(
        update=update, view=screens.time_prompt_view(lang, cb.REOPEN_END_EDITOR.with_id(meeting.db_id))
    )
    return ConversationMeetingState.END_DATETIME_CARD


# --- Typed input ---


async def save_end_datetime(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    end_dt: dt.datetime,
    *,
    lang: str,
    input_source: str,
) -> ConversationMeetingState:
    """Shared tail of both typed-end paths: apply, answer with a fresh card, fan out."""
    rules.apply_end_datetime(meeting, end_dt, input_source=input_source)
    state = await show_end_card(context, update, meeting, lang, as_reply=True)

    await context.api.update_meeting_messages(meeting=meeting)
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "end_datetime"})
    return state


async def reply_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    For the message paths (a sent datetime entity or an HH:MM time): the upsell is sent as a
    reply carrying the Collaborate button and a button back to the card. It is checked apart from
    the other validations so it can carry the Collaborate button at all; a raised horizon is
    exactly what Collaborate offers.
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

        if meeting.datetime is None:
            return await recover_typed_without_start_time(context, update, user, meeting)

        if error := rules.validate_end_datetime(unix_time, meeting, user.lang):
            return await show_end_card(context, update, meeting, user.lang, error=RichContent(error), as_reply=True)

        if await reply_end_beyond_horizon(context, update, meeting, unix_time):
            return ConversationMeetingState.END_DATETIME_CARD

        return await save_end_datetime(
            context, update, meeting, unix_time, lang=user.lang, input_source="datetime_entity"
        )


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

        if meeting.datetime is None:
            return await recover_typed_without_start_time(context, update, user, meeting)

        if meeting.end_datetime is None:
            state = await show_end_card(
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
                field="end",
                conversation_state=ConversationMeetingState.END_DATETIME_CARD.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "time_without_date"}
            )
            return state

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            state = await show_end_card(
                context, update, meeting, lang, error=CommonMessages.TIME_INVALID_VALUE.rich(lang=lang), as_reply=True
            )
            log.info(
                "Meeting datetime input rejected",
                user_id=user.db_id,
                reason="invalid_time_value",
                field="end",
                conversation_state=ConversationMeetingState.END_DATETIME_CARD.name,
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "invalid_time"}
            )
            return state

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]))
        date_to_set = user.datetime_in_tz(meeting.end_datetime).date()
        proposed_end = local_to_utc(date_to_set, user_time, user.settings.tz)

        if error := rules.validate_end_datetime(proposed_end, meeting, lang):
            return await show_end_card(context, update, meeting, lang, error=RichContent(error), as_reply=True)

        if await reply_end_beyond_horizon(context, update, meeting, proposed_end):
            return ConversationMeetingState.END_DATETIME_CARD

        return await save_end_datetime(context, update, meeting, proposed_end, lang=lang, input_source="time_message")


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

        if meeting.datetime is None:
            return await recover_typed_without_start_time(context, update, user, meeting)

        error = CommonMessages.DATETIME_INPUT_INVALID.rich(lang=user.lang, datetime_link=datetime_link_content())
        state = await show_end_card(context, update, meeting, user.lang, error=error, as_reply=True)
        log.info(
            "Meeting datetime input rejected",
            user_id=user.db_id,
            reason="wrong_datetime_format",
            field="end",
            conversation_state=ConversationMeetingState.END_DATETIME_CARD.name,
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_end_datetime_format"}
        )
        return state


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.END_EDITOR_CONVERSATION,
    entry_points_handler_names=[
        EditMeetingHandlerId.OPEN_END_EDITOR,
        # Every button that redraws the card is also an entry point: the card outlives the
        # conversation (the upsell reply stays in the chat, and any old card can be tapped), and
        # reviving the conversation on any tap is what keeps typed input working afterwards. PTB
        # matches entry points ahead of the current state's handlers.
        EditMeetingHandlerId.REOPEN_END_EDITOR,
        EditMeetingHandlerId.NAVIGATE_END_CALENDAR,
        EditMeetingHandlerId.PICK_END_DATE,
        EditMeetingHandlerId.OPEN_END_TIME_PROMPT,
    ],
    states={
        ConversationMeetingState.END_DATETIME_CARD: [
            EditMeetingHandlerId.TYPE_END_DATETIME,
            EditMeetingHandlerId.TYPE_END_TIME,
            EditMeetingHandlerId.CANCEL_END_EDIT,
            EditMeetingHandlerId.REJECT_END_DATETIME,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL],
)


# --- Removing the end time ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_END_TIME_CALLBACK, callback_data=cb.DELETE_MEETING_END_TIME
)
@with_session
async def callback_query_remove_end_time(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_END_TIME.parse(context.match), EditMeetingHandlerId.REMOVE_END_TIME_CALLBACK
    )
    user = await guards.current_user(update, session)

    await guards.meeting(session, user, callback_data.id, "remove_end_time", context)

    # The confirmation leaves the card flow, so the flow's typed-input context goes with it.
    cleanup_states(context)
    log.info("Meeting destructive action prompted", user_id=user.db_id, action=REMOVE_END_TIME_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditWhenMessages.REMOVE_END_CONFIRMATION.rich(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_END_TIME.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_END_TIME.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_END_TIME_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_END_TIME
)
@with_session(write=True)
async def callback_query_confirm_remove_end_time(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_END_TIME.parse(context.match),
        EditMeetingHandlerId.CONFIRM_REMOVE_END_TIME_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_end_time", context)

    # Written before the wipe, the last moment the value it records still exists.
    log.info(
        "Meeting end time removed",
        user_id=user.db_id,
        reason="owner_confirmed",
        previous_end_datetime=meeting.end_datetime,
    )

    meeting.end_datetime = None

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "end_datetime"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_END_TIME_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_END_TIME
)
@with_session
async def callback_query_decline_remove_end_time(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_END_TIME.parse(context.match),
        EditMeetingHandlerId.DECLINE_REMOVE_END_TIME_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_end_time", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_END_TIME_ACTION)

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
