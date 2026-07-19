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
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link, render
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingDisplayMessages, MeetingEditDateTimeMessages
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view

from ..utils import scheduling_horizon_rejection
from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import DateTimeEntityFilter, cleanup_states, is_in_past, prepend_error, safe_anchor_date, to_utc

# This module manages the start-time editing sub-flow for a meeting.
#
# States:
#   EDIT_DATETIME -- entry screen: [Date] [Time] + [Cancel]
#     * Sending a date_time entity -> save + END (shows when_view)
#     * [Date] -> calendar (EDIT_DATE state)
#     * [Time] -> HH:MM prompt (EDIT_TIME state)
#     * [Cancel] -> cleanup + END (exits to When hub)
#   EDIT_DATE -- calendar view: click a date or press [Back]
#     * Clicking a date when no time set -> saves date at 23:59, prompts for time (EDIT_TIME)
#     * Clicking a date when time already set -> updates date, re-shows entry (EDIT_DATETIME)
#     * [Back] -> re-shows entry (EDIT_DATETIME, no cleanup -- navigating within conversation)
#   EDIT_TIME -- HH:MM prompt
#     * Valid HH:MM -> save + END (shows when_view)
#     * [Cancel] fallback -> cleanup + END

log = structlog.get_logger(__name__)


# --- Shared helpers ---


def validate_start_datetime(start_dt: dt.datetime, meeting: Meetup, lang: str) -> str | None:
    """Return an error message string if start_dt is invalid, or None if valid.

    Symmetric with ``validate_end_datetime`` in edit_meeting_duration.py. The scheduling horizon is
    not checked here: date-pick call sites check it separately so the rejection can carry the
    Collaborate upsell, and time-only edits skip it entirely so a grandfathered far-future meeting
    can still have its time adjusted.
    """
    if is_in_past(start_dt, meeting):
        return MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=lang)
    return None


async def reject_beyond_horizon(context: TMitupContext, update: Update, meeting: Meetup, start_dt: dt.datetime) -> bool:
    """Return True, having informed the owner, when start_dt is beyond their scheduling horizon.

    The tapped calendar message is edited into the rejection carrying the Collaborate button and a
    button back to the calendar, so the upsell replaces the screen instead of stacking an alert on
    top of it; the conversation stays in EDIT_DATE, where the calendar button is handled.
    """
    rejection = scheduling_horizon_rejection(meeting.owner, start_dt)
    if rejection is None:
        return False
    lang = meeting.owner.lang
    today = meeting.owner.now_in_tz().date()
    calendar_button = ButtonConfig(
        text=ButtonMessages.DATE.back(lang=lang),
        callback_data=cb.EDIT_MEETING_DATE.with_id(meeting.db_id).with_date(today),
    )
    view = supporter_upsell_view(rejection, lang).with_context_menu([[calendar_button]])
    await context.api.edit_message(update=update, view=view)
    return True


def prepend_end_cleared_notice(*, lang: str, base_message: str | FormattedText) -> FormattedText:
    return MeetingEditDateTimeMessages.END_CLEARED_BY_START.get(lang=lang).append("\n\n").append(base_message)


def build_edit_time_prompt_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the start-time HH:MM prompt view (Cancel button).

    A date-first flow (``meeting.datetime`` still unset) leads with the 23:59-default note. When
    ``error`` is given it is prepended as a leading paragraph, so a message that failed validation
    can be answered by resending this prompt with the error on top rather than a bare, button-less
    error.
    """
    description: str | FormattedText = CommonMessages.TIME_PROMPT.get(lang=lang)
    if meeting.datetime is None:
        description = (
            MeetingEditDateTimeMessages.TIME_DATE_DEFAULT_NOTE.get(lang=lang)
            .append("\n\n")
            .append(CommonMessages.TIME_PROMPT.get(lang=lang))
        )
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.CANCEL_EDIT_START_TIME.with_id(meeting.db_id),
                )
            ]
        ],
    )


async def show_edit_time_prompt(context: TMitupContext, update: Update, meeting: Meetup) -> ConversationMeetingState:
    lang = meeting.lang
    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_EDIT_START_TIME.with_id(meeting.db_id),
    )
    await context.api.edit_message(update=update, view=build_edit_time_prompt_view(meeting, lang))
    return ConversationMeetingState.EDIT_TIME


# --- EDIT_DATETIME entry ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
    callback_data=cb.SET_MEETING_START_TIME,
    bindable=False,
)
@with_session
async def callback_query_date_time_entry(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_callback_data(
        cb.SET_MEETING_START_TIME.parse(context.match), EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK
    )

    user = await guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Edit date and time", update, context
        )
    ) is None:
        return None

    meeting_id = meeting.db_id
    lang = user.lang
    today = user.now_in_tz().date()

    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_EDIT_START_TIME.with_id(meeting_id),
    )

    await context.api.edit_message(update=update, view=build_edit_datetime_entry_view(meeting, lang, today))

    return ConversationMeetingState.EDIT_DATETIME


def build_edit_datetime_entry_view(
    meeting: Meetup, lang: str, today: dt.date, *, error: str | FormattedText | None = None
) -> MitupView:
    """Build the start datetime entry view (Date / Time buttons + When back button).

    When ``error`` is given it is prepended as a leading paragraph, so a message that failed
    validation can be answered by resending this prompt with the error on top rather than a
    bare, button-less error.
    """
    meeting_id = meeting.db_id
    datetime_link = build_datetime_link()
    keyboard: list[list[ButtonConfig]] = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get_text(lang=lang),
                callback_data=cb.EDIT_MEETING_DATE.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get_text(lang=lang),
                callback_data=cb.EDIT_MEETING_TIME.with_id(meeting_id),
            ),
        ],
    ]
    description: str | FormattedText = MeetingEditDateTimeMessages.DESCRIPTION.get(
        lang=lang, datetime_link=datetime_link
    )
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=keyboard,
    ).with_back_button(ButtonMessages.WHEN, lang, cb.CANCEL_EDIT_START_TIME.with_id(meeting_id))


# --- Cancel handler (exits to When hub) ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
    callback_data=cb.CANCEL_EDIT_START_TIME,
    bindable=False,
)
@with_session
async def callback_query_cancel_start_time(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    callback_data = guards.valid_callback_data(
        cb.CANCEL_EDIT_START_TIME.parse(context.match), EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK
    )

    user = await guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Cancel start time edit", update, context
        )
    ) is None:
        cleanup_states(context)
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))
    cleanup_states(context)
    return ConversationHandler.END


# --- EDIT_DATE: Calendar navigation ---


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.DATE_CALLBACK, callback_data=cb.EDIT_MEETING_DATE)
@with_session
async def callback_query_edit_meeting_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    assert context.matches is not None

    callback_data = guards.valid_date_callback_data(
        cb.EDIT_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DATE_CALLBACK
    )

    user = await guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return None

    now_in_user_timezone = meeting.owner.now_in_tz()
    today_in_user_timezone = now_in_user_timezone.date()
    anchor_date = safe_anchor_date(meeting.datetime, now_in_user_timezone)

    current_date = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    meeting_date_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else None
    log.debug(
        "Rendering calendar view",
        anchor_date=anchor_date,
        current_date=current_date,
        meeting_date=meeting_date_in_tz,
        now_in_user_timezone=now_in_user_timezone,
        callback_date=callback_data.date,
    )

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            guards.render_context(user, update, context),
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.datetime is None,
        ),
    )

    return ConversationMeetingState.EDIT_DATE


# --- EDIT_DATE: Back handler (returns to EDIT_DATETIME) ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
    callback_data=cb.EDIT_MEETING,
    bindable=False,
)
@with_session
async def callback_query_back_to_edit_datetime(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING.parse(context.match), EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK
    )

    user = await guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Back to edit datetime", update, context
        )
    ) is None:
        return None

    today = meeting.owner.now_in_tz().date()
    await context.api.edit_message(update=update, view=build_edit_datetime_entry_view(meeting, user.lang, today))
    return ConversationMeetingState.EDIT_DATETIME


# --- EDIT_DATE: Date selection ---


async def handle_first_datetime_set(
    context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
) -> ConversationMeetingState:
    proposed_start = dt.datetime.combine(cb_date, dt.time(23, 59, tzinfo=meeting.timezone)).astimezone(dt.UTC)
    if error := validate_start_datetime(proposed_start, meeting, meeting.lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.EDIT_DATE
    if await reject_beyond_horizon(context, update, meeting, proposed_start):
        return ConversationMeetingState.EDIT_DATE

    meeting.datetime = proposed_start
    end_cleared = meeting.enforce_datetime_ordering()

    lang = meeting.lang

    if end_cleared:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=lang),
            show_alert=True,
        )

    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingEditDateTimeMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_EDIT_START_TIME.with_id(meeting.db_id),
    )
    done_button = ButtonConfig(
        text=ButtonMessages.DONE.get_text(lang=lang),
        callback_data=cb.CANCEL_EDIT_START_TIME.with_id(meeting.db_id),
    )
    assert meeting.datetime is not None
    datetime_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT")
    description = MeetingEditDateTimeMessages.DATE_ADDED_TIME_PROMPT.get(
        lang=lang,
        datetime=render(t"{datetime_entity}"),
    )
    if end_cleared:
        description = prepend_end_cleared_notice(lang=lang, base_message=description)

    view = MitupView(
        description=description,
        keyboard=[[done_button]],
    )
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return ConversationMeetingState.EDIT_TIME


async def handle_datetime_update(
    context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
) -> ConversationMeetingState:
    local_time = to_utc(cast(dt.datetime, meeting.datetime)).astimezone(meeting.timezone).time()
    proposed_start = dt.datetime.combine(cb_date, local_time, tzinfo=meeting.timezone).astimezone(dt.UTC)
    if error := validate_start_datetime(proposed_start, meeting, meeting.lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.EDIT_DATE
    if await reject_beyond_horizon(context, update, meeting, proposed_start):
        return ConversationMeetingState.EDIT_DATE

    meeting.datetime = proposed_start
    end_cleared = meeting.enforce_datetime_ordering()

    if end_cleared:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=meeting.lang),
            show_alert=True,
        )

    assert meeting.datetime is not None
    datetime_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT")
    today = meeting.owner.now_in_tz().date()
    context_message = MeetingEditDateTimeMessages.DATE_UPDATED.get(
        lang=meeting.lang, datetime=render(t"{datetime_entity}")
    )
    if end_cleared:
        context_message = prepend_end_cleared_notice(lang=meeting.lang, base_message=context_message)

    await context.api.edit_message(
        update=update,
        view=build_edit_datetime_entry_view(meeting, meeting.lang, today).with_context(context_message),
    )
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
    return ConversationMeetingState.EDIT_DATETIME


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.SET_DATE_CALLBACK, callback_data=cb.SET_MEETING_DATE)
@with_session(write=True)
async def callback_query_set_meeting_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_date_callback_data(
        cb.SET_MEETING_DATE.parse(context.match), EditMeetingHandlerId.SET_DATE_CALLBACK
    )

    user = await guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return ConversationHandler.END

    if meeting.datetime is None:
        return await handle_first_datetime_set(context, update, meeting, callback_data.date)
    return await handle_datetime_update(context, update, meeting, callback_data.date)


# --- Time editing ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.EDIT_TIME_CALLBACK, callback_data=cb.EDIT_MEETING_TIME, bindable=False
)
@with_session
async def callback_query_set_meeting_time(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_TIME.parse(context.match), EditMeetingHandlerId.EDIT_TIME_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit time",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    return await show_edit_time_prompt(context, update, meeting)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_session(write=True)
async def date_time_entity_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    message = guards.message(update)
    entities = message.entities or []
    date_entity = next(e for e in entities if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_TIME, ensure_clean=False) as meeting_id:
        current_user = await guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, current_user, meeting_id, "Set datetime from entity", update, context
        )

        if meeting is None:
            return ConversationHandler.END

        if error := validate_start_datetime(unix_time, meeting, current_user.lang):
            today = meeting.owner.now_in_tz().date()
            await context.api.send_message(
                update=update, view=build_edit_datetime_entry_view(meeting, current_user.lang, today, error=error)
            )
            return ConversationMeetingState.EDIT_DATETIME

        # The horizon rejection is checked apart from the other validations so it can carry the
        # Collaborate button; a raised horizon is exactly what Collaborate offers. The back button
        # re-renders the Date & Time entry screen the message was sent from.
        if rejection := scheduling_horizon_rejection(meeting.owner, unix_time):
            back_button = ButtonConfig(
                text=ButtonMessages.DATE_TIME.back(lang=current_user.lang),
                callback_data=cb.EDIT_MEETING.with_id(meeting.db_id),
            )
            view = supporter_upsell_view(rejection, current_user.lang).with_context_menu([[back_button]])
            await context.api.send_message(update=update, view=view)
            return ConversationMeetingState.EDIT_DATETIME

        meeting.datetime = unix_time
        end_cleared = meeting.enforce_datetime_ordering()

        assert meeting.datetime is not None
        datetime_entity = EntityDateTime(
            MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT"
        )
        context_message = MeetingEditDateTimeMessages.DATE_UPDATED.get(
            lang=current_user.lang, datetime=render(t"{datetime_entity}")
        )
        if end_cleared:
            context_message = prepend_end_cleared_notice(lang=current_user.lang, base_message=context_message)

        view = meeting_views.when_view(meeting).with_context(context_message)

        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(meeting=meeting)

        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
        cleanup_states(context)
        return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_session(write=True)
async def set_time_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    time_info = cast(Match, context.match).groupdict()

    with context.meeting_id(ContextId.EDIT_MEETING_TIME, ensure_clean=False) as meeting_id:
        current_user = await guards.current_user(update, session)
        meeting = await guards.meeting_accessible(session, current_user, meeting_id, "Set time", update, context)

        if meeting is None:
            return ConversationHandler.END

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            await context.api.send_message(
                update=update,
                view=build_edit_time_prompt_view(
                    meeting, current_user.lang, error=CommonMessages.TIME_INVALID_VALUE.get(lang=current_user.lang)
                ),
            )
            context.emit_metric(MetricKey.ERROR.with_prefix("InvalidTime"), 1)
            return ConversationMeetingState.EDIT_TIME

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=current_user.settings.tz)
        date_to_set = current_user.datetime_in_tz(meeting.datetime or dt.datetime.now(dt.UTC)).date()
        proposed_start = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        if error := validate_start_datetime(proposed_start, meeting, current_user.lang):
            await context.api.send_message(
                update=update, view=build_edit_time_prompt_view(meeting, current_user.lang, error=error)
            )
            return ConversationMeetingState.EDIT_TIME

        meeting.datetime = proposed_start
        end_cleared = meeting.enforce_datetime_ordering()

        assert meeting.datetime is not None
        datetime_entity = EntityDateTime(
            MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT"
        )
        context_message = MeetingEditDateTimeMessages.TIME_SUCCESS.get(
            lang=current_user.lang, datetime=render(t"{datetime_entity}")
        )
        if end_cleared:
            context_message = prepend_end_cleared_notice(lang=current_user.lang, base_message=context_message)

        view = meeting_views.when_view(meeting).with_context(context_message)

        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(meeting=meeting)

        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "datetime"})
        cleanup_states(context)
        return ConversationHandler.END


async def fallback_answer(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_TIME, ensure_clean=False) as meeting_id:
        current_user = await guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, current_user, meeting_id, "Wrong time input", update, context
        )
        if meeting is None:
            return ConversationHandler.END

        await context.api.send_message(
            update=update,
            view=build_edit_time_prompt_view(
                meeting, current_user.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=current_user.lang)
            ),
        )
        context.emit_metric(MetricKey.ERROR.with_prefix("WrongTimeFormat"), 1)
        return ConversationMeetingState.EDIT_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_FORMAT,
    bindable=False,
    filters=filters.TEXT & ~filters.COMMAND,
)
@with_session
async def wrong_message_sent_for_time(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    return await fallback_answer(session, update, context)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_MESSAGE,
    bindable=False,
    filters=~filters.TEXT,
)
@with_session
async def wrong_message_type_sent_for_time(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    return await fallback_answer(session, update, context)


async def datetime_state_fallback_answer(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_TIME, ensure_clean=False) as meeting_id:
        current_user = await guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, current_user, meeting_id, "Wrong datetime input", update, context
        )
        if meeting is None:
            return ConversationHandler.END

        today = meeting.owner.now_in_tz().date()
        error = CommonMessages.DATETIME_INVALID.get(lang=current_user.lang, datetime_link=build_datetime_link())
        await context.api.send_message(
            update=update, view=build_edit_datetime_entry_view(meeting, current_user.lang, today, error=error)
        )
        context.emit_metric(MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), 1)
        return ConversationMeetingState.EDIT_DATETIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
    bindable=False,
    filters=filters.TEXT & ~filters.COMMAND,
)
@with_session
async def datetime_wrong_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    return await datetime_state_fallback_answer(session, update, context)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
    bindable=False,
    filters=~filters.TEXT,
)
@with_session
async def datetime_wrong_message_type_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    return await datetime_state_fallback_answer(session, update, context)


# --- Conversation registration ---


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK],
    states={
        ConversationMeetingState.EDIT_DATETIME: [
            EditMeetingHandlerId.DATE_CALLBACK,
            EditMeetingHandlerId.EDIT_TIME_CALLBACK,
            EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
            EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
            EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
            # Reachable from the beyond-horizon rejection's back button, which re-renders the
            # entry screen without leaving the conversation.
            EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.EDIT_DATE: [
            EditMeetingHandlerId.DATE_CALLBACK,
            EditMeetingHandlerId.SET_DATE_CALLBACK,
            EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.EDIT_TIME: [
            EditMeetingHandlerId.SET_TIME_MESSAGE,
            EditMeetingHandlerId.WRONG_TIME_FORMAT,
            EditMeetingHandlerId.WRONG_TIME_MESSAGE,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK],
)
