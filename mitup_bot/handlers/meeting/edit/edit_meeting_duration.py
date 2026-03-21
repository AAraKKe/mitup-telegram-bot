import datetime as dt
import logging
from re import Match
from typing import cast

from sqlmodel import Session
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link, render
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import DateTimeEntityFilter, cleanup_states, safe_anchor_date

# This module manages the duration editing sub-flow for a meeting.
#
# The duration conversation auto-chains through setting start datetime (if missing)
# then end datetime. Both datetime selections use the same UX: calendar + time picker,
# or datetime entity message.
#
# States:
#   DURATION_SET_START_DATETIME -- only when meeting.datetime is None
#     * Date button -> DURATION_SET_START_DATE
#     * Time button -> DURATION_SET_START_TIME
#     * datetime entity -> set start -> EDIT_END_DATETIME
#     * Cancel -> END
#     * wrong message fallback -> stay
#
#   DURATION_SET_START_DATE
#     * Calendar nav -> stay
#     * Date selected (no time yet) -> DURATION_SET_START_TIME
#     * Date selected (time exists) -> EDIT_END_DATETIME
#     * Back -> DURATION_SET_START_DATETIME
#
#   DURATION_SET_START_TIME
#     * HH:MM message -> set start -> EDIT_END_DATETIME
#     * datetime entity -> set start -> EDIT_END_DATETIME
#     * wrong message fallback -> stay
#
#   EDIT_END_DATETIME -- always entered (directly or after start is set)
#     * Date button -> EDIT_END_DATE
#     * Time button -> EDIT_END_TIME
#     * datetime entity -> validate end > start -> save -> END
#     * Cancel -> END
#     * wrong message fallback -> stay
#
#   EDIT_END_DATE
#     * Calendar nav -> stay
#     * Date selected (no end time yet) -> EDIT_END_TIME
#     * Date selected (end time exists) -> validate -> EDIT_END_DATETIME
#     * Back -> EDIT_END_DATETIME
#
#   EDIT_END_TIME
#     * HH:MM message -> validate end > start -> save -> END
#     * datetime entity -> validate -> save -> END
#     * wrong message fallback -> stay


# --- Standalone handlers (not part of conversation) ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_DURATION
)
@with_async_session
async def callback_query_edit_meeting_duration(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_edit_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_ENTRY_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "edit_meeting_duration", update, context)
    if meeting is None:
        return

    await context.api.edit_message(update=update, view=meeting.duration_view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_CLEAR_CALLBACK, callback_data=cb.CLEAR_MEETING_DURATION
)
@with_async_session
async def callback_query_clear_meeting_duration(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_clear_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.CLEAR_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_CLEAR_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "clear_meeting_duration", update, context)
    if meeting is None:
        return

    meeting.end_datetime = None
    meeting.lock_on_start = False
    session.flush()

    response_view = meeting.duration_view.with_context(MeetingMessages.DURATION_CLEARED.get(lang=user.lang))

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(session=session, meeting=meeting)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCK_ON_START_CALLBACK, callback_data=cb.SET_MEETING_LOCK_ON_START
)
@with_async_session
async def callback_query_set_lock_on_start(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_set_lock_on_start")

    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_LOCK_ON_START.parse(context.match), EditMeetingHandlerId.LOCK_ON_START_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "set_lock_on_start", update, context)
    if meeting is None:
        return

    if meeting.end_datetime is None:
        await context.api.answer_callback_query(
            update,
            text=MeetingMessages.LOCK_ON_START_STALE_ALERT.get_text(lang=meeting.user_language),
            show_alert=True,
        )
        return

    meeting.lock_on_start = not meeting.lock_on_start
    session.flush()

    await context.api.edit_message(update=update, view=meeting.duration_view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


# --- Conversation entry ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_INPUT_CALLBACK, callback_data=cb.SET_MEETING_DURATION, bindable=False
)
@with_async_session
async def callback_query_set_meeting_duration(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    logging.debug("Enter into callback_query_set_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_INPUT_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "set_meeting_duration", update, context)
    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DURATION, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DURATION,
        MeetingMessages.EDIT_MEETING_DURATION_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
    )

    if meeting.datetime is None:
        await context.api.edit_message(
            update=update,
            view=build_start_datetime_entry_view(meeting_id, user.lang, user.now_in_tz().date()),
        )
        return ConversationMeetingState.DURATION_SET_START_DATETIME

    return await show_end_datetime_entry(context, update, meeting, user.lang)


# --- Cancel handler ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_CANCEL_CALLBACK, callback_data=cb.CANCEL_EDIT_MEETING_DURATION, bindable=False
)
@with_async_session
async def callback_query_cancel_edit_duration(session: Session, update: Update, context: TMitupContext) -> int:
    logging.debug("Enter into callback_query_cancel_edit_duration")

    context.clean_all_user_data()

    meeting_id = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_CANCEL_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session, user, meeting_id, "cancel_edit_meeting_duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=meeting.duration_view)

    return ConversationHandler.END


# --- DURATION_SET_START_DATETIME state ---


def build_start_datetime_entry_view(meeting_id: int, lang: str, today: dt.date) -> MitupView:
    keyboard: list[list[ButtonConfig]] = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get(lang=lang),
                callback_data=cb.DURATION_EDIT_START_DATE.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get(lang=lang),
                callback_data=cb.DURATION_EDIT_START_TIME.with_id(meeting_id),
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.CANCEL.get(lang=lang),
                callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
            ),
        ],
    ]
    return MitupView(
        description=MeetingMessages.DURATION_NO_START_DATETIME.get(lang=lang, datetime_link=build_datetime_link()),
        keyboard=keyboard,
    )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_START_DATETIME_ENTITY_MESSAGE,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_async_session
async def duration_start_datetime_entity_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into duration_start_datetime_entity_handler")

    message = guards.message(update)
    entities = message.entities or []
    date_entity = next(e for e in entities if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_DURATION, ensure_clean=False) as meeting_id:
        user = guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, user, meeting_id, "Set start datetime from entity", update, context
        )
        if meeting is None:
            return ConversationHandler.END

        meeting.datetime = unix_time
        session.add(meeting)
        session.flush()

        await context.api.update_meeting_messages(
            session=session,
            meeting=meeting,
            current_message=meeting.message_from_update(update),
            skip_current=True,
        )

        return await show_end_datetime_entry(
            context,
            update,
            meeting,
            user.lang,
            context_message=MeetingMessages.DURATION_START_DATETIME_SET.get(lang=user.lang),
        )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_START_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_async_session
async def duration_start_wrong_input_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = guards.current_user(update, session)
    datetime_link = build_datetime_link()
    await context.api.send_message(
        update=update,
        view=MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=user.lang, datetime_link=datetime_link),
    )
    context.emit_metric(MetricKey.ERROR.with_prefix("WrongStartDatetimeFormat"), 1)
    return ConversationMeetingState.DURATION_SET_START_DATETIME


# --- DURATION_SET_START_DATE state ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_START_DATE_NAV_CALLBACK, callback_data=cb.DURATION_EDIT_START_DATE, bindable=False
)
@with_async_session
async def callback_query_duration_start_date_nav(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_duration_start_date_nav")

    callback_data = guards.valid_date_callback_data(
        cb.DURATION_EDIT_START_DATE.parse(context.match), EditMeetingHandlerId.DURATION_START_DATE_NAV_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Duration start date nav", update, context
    )
    if meeting is None:
        return None

    now_in_user_tz = meeting.owner.now_in_tz()
    today = now_in_user_tz.date()
    anchor_date = safe_anchor_date(meeting.datetime, now_in_user_tz)
    current_date = callback_data.date if today <= callback_data.date else today

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            lang=user.lang,
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.datetime is None,
            set_date_callback=cb.DURATION_SET_START_DATE,
            nav_callback=cb.DURATION_EDIT_START_DATE,
            back_callback=cb.CANCEL_EDIT_MEETING_DURATION,
        ),
    )
    return ConversationMeetingState.DURATION_SET_START_DATE


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_BACK_TO_START_DATETIME_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_DURATION,
    bindable=False,
)
@with_async_session
async def callback_query_back_to_start_datetime(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    """Navigate back from the start-date calendar to the start datetime entry view."""
    logging.debug("Enter into callback_query_back_to_start_datetime")

    callback_data = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_DURATION.parse(context.match),
        EditMeetingHandlerId.DURATION_BACK_TO_START_DATETIME_CALLBACK,
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Back to start datetime", update, context
    )
    if meeting is None:
        cleanup_states(context)
        return ConversationHandler.END

    await context.api.edit_message(
        update=update,
        view=build_start_datetime_entry_view(meeting.db_id, user.lang, user.now_in_tz().date()),
    )
    return ConversationMeetingState.DURATION_SET_START_DATETIME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_START_SET_DATE_CALLBACK, callback_data=cb.DURATION_SET_START_DATE, bindable=False
)
@with_async_session
async def callback_query_duration_start_set_date(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_duration_start_set_date")

    callback_data = guards.valid_date_callback_data(
        cb.DURATION_SET_START_DATE.parse(context.match), EditMeetingHandlerId.DURATION_START_SET_DATE_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Set start date in duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    if meeting.datetime is None:
        meeting.datetime = dt.datetime.combine(callback_data.date, dt.time(0, 0, tzinfo=meeting.timezone)).astimezone(
            dt.UTC
        )
        session.add(meeting)
        session.flush()

        return await show_time_prompt(
            context,
            update,
            meeting,
            datetime_value=meeting.datetime,
            return_state=ConversationMeetingState.DURATION_SET_START_TIME,
        )

    meeting.datetime = dt.datetime.combine(
        callback_data.date,
        meeting.datetime.time(),
        tzinfo=dt.UTC,
    )
    session.add(meeting)
    session.flush()

    await context.api.update_meeting_messages(
        session=session, meeting=meeting, current_message=meeting.message_from_update(update), skip_current=True
    )
    return await show_end_datetime_entry(
        context,
        update,
        meeting,
        user.lang,
        context_message=MeetingMessages.DURATION_START_DATETIME_SET.get(lang=user.lang),
    )


# --- DURATION_SET_START_TIME state ---


async def show_time_prompt(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    *,
    datetime_value: dt.datetime,
    return_state: ConversationMeetingState,
) -> ConversationMeetingState:
    lang = meeting.lang
    datetime_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), datetime_value, "DT")
    view = MitupView(
        description=MeetingMessages.NEW_DATE_SET_SUCCESS.get(lang=lang, datetime=render(t"{datetime_entity}")),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get(lang=lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)
    return return_state


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_START_TIME_CALLBACK, callback_data=cb.DURATION_EDIT_START_TIME, bindable=False
)
@with_async_session
async def callback_query_duration_start_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_duration_start_time")

    callback_data = guards.valid_callback_data(
        cb.DURATION_EDIT_START_TIME.parse(context.match), EditMeetingHandlerId.DURATION_START_TIME_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Edit start time in duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.DURATION_SET_START_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_START_SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_async_session
async def duration_start_set_time_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into duration_start_set_time_handler")

    time_info = cast(Match, context.match).groupdict()

    if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
        user = guards.current_user(update, session)
        await context.api.send_message(update=update, view=MeetingMessages.INVALID_TIME.get(lang=user.lang))
        context.emit_metric(MetricKey.ERROR.with_prefix("InvalidTime"), 1)
        return ConversationMeetingState.DURATION_SET_START_TIME

    with context.meeting_id(ContextId.EDIT_MEETING_DURATION, ensure_clean=False) as meeting_id:
        user = guards.current_user(update, session)
        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=user.settings.tz)

        meeting = await guards.meeting_accessible(session, user, meeting_id, "Set start time", update, context)
        if meeting is None:
            return ConversationHandler.END

        date_to_set = user.datetime_in_tz(meeting.datetime or dt.datetime.now(dt.UTC)).date()
        meeting.datetime = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)
        session.add(meeting)
        session.flush()

        await context.api.update_meeting_messages(
            session=session, meeting=meeting, current_message=meeting.message_from_update(update), skip_current=True
        )
        return await show_end_datetime_entry(
            context,
            update,
            meeting,
            user.lang,
            context_message=MeetingMessages.DURATION_START_DATETIME_SET.get(lang=user.lang),
            use_send=True,
        )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_START_TIME_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_async_session
async def duration_start_time_wrong_input_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = guards.current_user(update, session)
    await context.api.send_message(update=update, view=MeetingMessages.WRONG_TIME_FORMAT.get(lang=user.lang))
    context.emit_metric(MetricKey.ERROR.with_prefix("WrongTimeFormat"), 1)
    return ConversationMeetingState.DURATION_SET_START_TIME


# --- EDIT_END_DATETIME state ---


async def show_end_datetime_entry(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    lang: str,
    *,
    context_message: str | FormattedText | None = None,
    use_send: bool = False,
) -> ConversationMeetingState:
    """Show the end datetime entry view. Transitions to EDIT_END_DATETIME state."""
    assert meeting.datetime is not None
    start_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.datetime, "DT")
    start_text = render(t"{start_entity}")

    datetime_link = build_datetime_link()
    if meeting.end_datetime is not None:
        end_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.end_datetime, "DT")
        description = MeetingMessages.EDIT_END_DATETIME_PROMPT.get(
            lang=lang, start_datetime=start_text, end_datetime=render(t"{end_entity}"), datetime_link=datetime_link
        )
    else:
        description = MeetingMessages.SET_END_DATETIME_PROMPT.get(
            lang=lang, start_datetime=start_text, datetime_link=datetime_link
        )

    context.store_meeting_id(ContextId.EDIT_MEETING_END_DATETIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_END_DATETIME,
        MeetingMessages.EDIT_MEETING_DURATION_ON_EXIT.get(lang=lang),
        cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
    )

    meeting_id = meeting.db_id
    today = meeting.owner.now_in_tz().date()
    keyboard: list[list[ButtonConfig]] = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get(lang=lang),
                callback_data=cb.EDIT_MEETING_END_DATE.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get(lang=lang),
                callback_data=cb.EDIT_MEETING_END_TIME.with_id(meeting_id),
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.CANCEL.get(lang=lang),
                callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
            ),
        ],
    ]

    view = MitupView(description=description, keyboard=keyboard)
    if context_message is not None:
        view = view.with_context(context_message)

    if use_send:
        await context.api.send_message(update=update, view=view)
    else:
        await context.api.edit_message(update=update, view=view)

    return ConversationMeetingState.EDIT_END_DATETIME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_END_DATE_TIME, bindable=False
)
@with_async_session
async def callback_query_end_datetime_entry(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    logging.debug("Enter into callback_query_end_datetime_entry")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_DATE_TIME.parse(context.match), EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, callback_data.id, "Edit end datetime", update, context)
    if meeting is None:
        return None

    return await show_end_datetime_entry(context, update, meeting, user.lang)


def validate_end_datetime(end_dt: dt.datetime, meeting: Meetup, lang: str) -> str | None:
    """Return an error message string if end_dt is invalid, or None if valid."""
    assert meeting.datetime is not None
    if end_dt <= meeting.datetime:
        return MeetingMessages.END_DATETIME_BEFORE_START.get_text(lang=lang)
    return None


async def save_end_datetime_and_finish(
    session: Session,
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    end_dt: dt.datetime,
    lang: str,
    *,
    use_send: bool = False,
) -> int:
    """Persist end_datetime, broadcast updates, and return ConversationHandler.END."""
    meeting.end_datetime = end_dt
    session.add(meeting)
    session.flush()

    response_view = meeting.duration_view

    if use_send:
        await context.api.send_message(update=update, view=response_view)
    else:
        await context.api.edit_message(update=update, view=response_view)
    await context.api.update_meeting_messages(session=session, meeting=meeting)

    cleanup_states(context)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_async_session
async def duration_end_datetime_entity_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into duration_end_datetime_entity_handler")

    message = guards.message(update)
    entities = message.entities or []
    date_entity = next(e for e in entities if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, user, meeting_id, "Set end datetime from entity", update, context
        )
        if meeting is None:
            return ConversationHandler.END

        if error := validate_end_datetime(unix_time, meeting, user.lang):
            await context.api.send_message(update=update, view=error)
            return ConversationMeetingState.EDIT_END_DATETIME

        return await save_end_datetime_and_finish(
            session, context, update, meeting, unix_time, user.lang, use_send=True
        )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_async_session
async def duration_end_wrong_input_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = guards.current_user(update, session)
    datetime_link = build_datetime_link()
    await context.api.send_message(
        update=update,
        view=MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=user.lang, datetime_link=datetime_link),
    )
    context.emit_metric(MetricKey.ERROR.with_prefix("WrongEndDatetimeFormat"), 1)
    return ConversationMeetingState.EDIT_END_DATETIME


# --- EDIT_END_DATE state ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK, callback_data=cb.EDIT_MEETING_END_DATE, bindable=False
)
@with_async_session
async def callback_query_duration_end_date_nav(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_duration_end_date_nav")

    callback_data = guards.valid_date_callback_data(
        cb.EDIT_MEETING_END_DATE.parse(context.match), EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, callback_data.id, "Duration end date nav", update, context)
    if meeting is None:
        return None

    now_in_user_tz = meeting.owner.now_in_tz()
    today = now_in_user_tz.date()
    anchor_date = safe_anchor_date(meeting.end_datetime, now_in_user_tz)
    current_date = callback_data.date if today <= callback_data.date else today

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            lang=user.lang,
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.end_datetime is None,
            set_date_callback=cb.SET_MEETING_END_DATE,
            nav_callback=cb.EDIT_MEETING_END_DATE,
            back_callback=cb.EDIT_MEETING_END_DATE_TIME,
        ),
    )
    return ConversationMeetingState.EDIT_END_DATE


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
    callback_data=cb.EDIT_MEETING_END_DATE_TIME,
    bindable=False,
)
@with_async_session
async def callback_query_back_to_end_datetime(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_back_to_end_datetime")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_DATE_TIME.parse(context.match),
        EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, callback_data.id, "Back to end datetime", update, context)
    if meeting is None:
        return None

    return await show_end_datetime_entry(context, update, meeting, user.lang)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK, callback_data=cb.SET_MEETING_END_DATE, bindable=False
)
@with_async_session
async def callback_query_duration_end_set_date(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_duration_end_set_date")

    callback_data = guards.valid_date_callback_data(
        cb.SET_MEETING_END_DATE.parse(context.match), EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Set end date in duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    if meeting.end_datetime is None:
        proposed_end = dt.datetime.combine(callback_data.date, dt.time(0, 0, tzinfo=meeting.timezone)).astimezone(
            dt.UTC
        )
        meeting.end_datetime = proposed_end
        session.add(meeting)
        session.flush()

        return await show_time_prompt(
            context,
            update,
            meeting,
            datetime_value=meeting.end_datetime,
            return_state=ConversationMeetingState.EDIT_END_TIME,
        )

    proposed_end = dt.datetime.combine(
        callback_data.date,
        meeting.end_datetime.time(),
        tzinfo=dt.UTC,
    )
    meeting.end_datetime = proposed_end
    session.add(meeting)
    session.flush()

    return await show_end_datetime_entry(context, update, meeting, user.lang)


# --- EDIT_END_TIME state ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_TIME_CALLBACK, callback_data=cb.EDIT_MEETING_END_TIME, bindable=False
)
@with_async_session
async def callback_query_duration_end_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_duration_end_time")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_TIME.parse(context.match), EditMeetingHandlerId.DURATION_END_TIME_CALLBACK
    )
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, callback_data.id, "Edit end time in duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.EDIT_END_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_async_session
async def duration_end_set_time_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into duration_end_set_time_handler")

    time_info = cast(Match, context.match).groupdict()

    if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
        user = guards.current_user(update, session)
        await context.api.send_message(update=update, view=MeetingMessages.INVALID_TIME.get(lang=user.lang))
        context.emit_metric(MetricKey.ERROR.with_prefix("InvalidTime"), 1)
        return ConversationMeetingState.EDIT_END_TIME

    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = guards.current_user(update, session)
        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=user.settings.tz)

        meeting = await guards.meeting_accessible(session, user, meeting_id, "Set end time", update, context)
        if meeting is None:
            return ConversationHandler.END

        date_to_set = user.datetime_in_tz(meeting.end_datetime or dt.datetime.now(dt.UTC)).date()
        proposed_end = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        if error := validate_end_datetime(proposed_end, meeting, user.lang):
            await context.api.send_message(update=update, view=error)
            return ConversationMeetingState.EDIT_END_TIME

        return await save_end_datetime_and_finish(
            session, context, update, meeting, proposed_end, user.lang, use_send=True
        )


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_async_session
async def duration_end_time_wrong_input_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = guards.current_user(update, session)
    await context.api.send_message(update=update, view=MeetingMessages.WRONG_TIME_FORMAT.get(lang=user.lang))
    context.emit_metric(MetricKey.ERROR.with_prefix("WrongTimeFormat"), 1)
    return ConversationMeetingState.EDIT_END_TIME


# --- Conversation registration ---


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.DURATION_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DURATION_INPUT_CALLBACK],
    states={
        ConversationMeetingState.DURATION_SET_START_DATETIME: [
            EditMeetingHandlerId.DURATION_START_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_START_TIME_CALLBACK,
            EditMeetingHandlerId.DURATION_START_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
            EditMeetingHandlerId.DURATION_START_WRONG_INPUT,
        ],
        ConversationMeetingState.DURATION_SET_START_DATE: [
            EditMeetingHandlerId.DURATION_START_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_START_SET_DATE_CALLBACK,
            EditMeetingHandlerId.DURATION_BACK_TO_START_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.DURATION_SET_START_TIME: [
            EditMeetingHandlerId.DURATION_START_SET_TIME_MESSAGE,
            EditMeetingHandlerId.DURATION_START_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_START_TIME_WRONG_INPUT,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        ],
        ConversationMeetingState.EDIT_END_DATETIME: [
            EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_END_TIME_CALLBACK,
            EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
            EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
        ],
        ConversationMeetingState.EDIT_END_DATE: [
            EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
            EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.EDIT_END_TIME: [
            EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
            EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL],
)
