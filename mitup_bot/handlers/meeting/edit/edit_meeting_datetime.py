import datetime as dt
import logging
from re import Match
from typing import cast

from sqlmodel import Session
from telegram import Message, MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, build_datetime_link, render
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import cleanup_states

# This module manages the date/time editing sub-flow for a meeting.
#
# States:
#   EDIT_DATETIME — entry screen: [Date] [Time] + optional [Delete] buttons + [Back]
#     · Sending a date_time entity → save + END
#     · [Date] → calendar (EDIT_DATE state)
#     · [Time] → HH:MM prompt (EDIT_TIME state)
#     · [Delete] → confirmation prompt (stays in EDIT_DATETIME)
#     · [Back] → cleanup + END (exits to Edit Meeting view)
#   EDIT_DATE — calendar view: click a date or press [Back]
#     · Clicking a date when no time set → saves date at 00:00, prompts for time (EDIT_TIME)
#     · Clicking a date when time already set → updates date, re-shows entry (EDIT_DATETIME)
#     · [Back] → re-shows entry (EDIT_DATETIME, no cleanup — navigating within conversation)
#   EDIT_TIME — HH:MM prompt
#     · Valid HH:MM → save + END
#     · [Cancel] fallback → cleanup + END
#
# The calendar view no longer has a [Delete] or [Back] button in the keyboard —
# those are handled by conversation-internal handlers registered under EDIT_DATE.
# The [Delete] button lives in the EDIT_DATETIME entry keyboard (second row),
# visible only when meeting.datetime is set.


# --- Filters ---


class DateTimeEntityFilter(filters.MessageFilter):
    """Accept messages that contain at least one ``date_time`` entity."""

    def filter(self, message: Message) -> bool:
        return any(e.type == MessageEntity.DATE_TIME for e in (message.entities or []))


# --- Shared helpers ---


def safe_anchor_date(meeting_datetime: dt.datetime | None, user_now: dt.datetime) -> dt.date:
    """
    Returns the anchor date for the calendar view. If the meeting has a datetime set, and it is
    in the future, return the date of the meeting. Otherwise return the current date.
    """
    if meeting_datetime:
        meeting_date = dt.date(meeting_datetime.year, meeting_datetime.month, meeting_datetime.day)
        return meeting_date if meeting_date >= user_now.date() else user_now.date()
    return user_now.date()


async def show_edit_time_prompt(context: TMitupContext, update: Update, meeting: Meetup) -> ConversationMeetingState:
    lang = meeting.lang
    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingMessages.EDIT_MEETING_TIME_ON_EXIT.get(lang=lang),
        cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
    )
    view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.EDIT_TIME


# --- EDIT_DATETIME entry ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
    callback_data=cb.EDIT_MEETING_DATE_TIME,
    bindable=False,
)
@with_async_session
async def callback_query_date_time_entry(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    logging.debug("Enter into callback_query_date_time_entry")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_DATE_TIME.parse(context.match), EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK
    )

    user = guards.current_user(update, session)
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
        MeetingMessages.EDIT_MEETING_TIME_ON_EXIT.get(lang=lang),
        cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
    )

    await context.api.edit_message(update=update, view=build_edit_datetime_entry_view(meeting, lang, today))

    return ConversationMeetingState.EDIT_DATETIME


def build_edit_datetime_entry_view(meeting: Meetup, lang: str, today: dt.date) -> MitupView:
    meeting_id = meeting.db_id
    datetime_link = build_datetime_link()
    keyboard: list[list[ButtonConfig]] = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get(lang=lang),
                callback_data=cb.EDIT_MEETING_DATE.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get(lang=lang),
                callback_data=cb.EDIT_MEETING_TIME.with_id(meeting_id),
            ),
        ],
    ]
    if meeting.datetime is not None:
        keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_DATE.get(lang=lang),
                    callback_data=cb.DELETE_MEETING_DATE.with_id(meeting_id),
                )
            ]
        )
    keyboard.append(
        [
            ButtonConfig(
                text=ButtonMessages.EDIT.back(lang=lang),
                callback_data=cb.EDIT_MEETING.with_id(meeting_id),
            ),
        ]
    )
    return MitupView(
        description=MeetingMessages.DATE_TIME_VIEW_MESSAGE.get(lang=lang, datetime_link=datetime_link),
        keyboard=keyboard,
    )


# --- EDIT_DATETIME: Back handler (exits conversation) ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.BACK_TO_EDIT_MEETING_CALLBACK,
    callback_data=cb.EDIT_MEETING,
    bindable=False,
)
@with_async_session
async def callback_query_back_to_edit_meeting(session: Session, update: Update, context: TMitupContext) -> int:
    logging.debug("Enter into callback_query_back_to_edit_meeting")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING.parse(context.match), EditMeetingHandlerId.BACK_TO_EDIT_MEETING_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Back to edit meeting", update, context
        )
    ) is None:
        cleanup_states(context)
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=meeting.edit_view)
    cleanup_states(context)
    return ConversationHandler.END


# --- EDIT_DATETIME: Delete datetime flow ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK,
    callback_data=cb.DELETE_MEETING_DATE,
    bindable=False,
)
@with_async_session
async def callback_query_delete_date_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        await guards.meeting_accessible(session, user, callback_data.id, "Delete date and time", update, context)
    ) is None:
        return None

    view = factory.confirmation_view(
        lang=user.lang,
        message=MeetingMessages.DELETE_DATE_CONFIRMATION.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_DATE.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_DATE.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.EDIT_DATETIME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK,
    callback_data=cb.CONFIRM_DELETE_MEETING_DATE,
    bindable=False,
)
@with_async_session
async def callback_query_confirm_delete_date_time(session: Session, update: Update, context: TMitupContext) -> int:
    logging.debug("Enter into callback_query_confirm_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Confirm delete date and time", update, context
        )
    ) is None:
        cleanup_states(context)
        return ConversationHandler.END

    meeting.datetime = None
    session.add(meeting)
    session.flush()

    view = meeting.edit_view.with_context(MeetingMessages.DATE_TIME_DELETED.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    cleanup_states(context)
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK,
    callback_data=cb.DECLINE_DELETE_MEETING_DATE,
    bindable=False,
)
@with_async_session
async def callback_query_decline_delete_date_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_decline_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Decline delete date and time", update, context
        )
    ) is None:
        return None

    today = meeting.owner.now_in_tz().date()
    await context.api.edit_message(
        update=update,
        view=build_edit_datetime_entry_view(meeting, user.lang, today).with_context(
            MeetingMessages.DELETE_DATE_DECLINE.get(lang=user.lang)
        ),
    )
    return ConversationMeetingState.EDIT_DATETIME


# --- EDIT_DATE: Calendar navigation ---


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.DATE_CALLBACK, callback_data=cb.EDIT_MEETING_DATE)
@with_async_session
async def callback_query_edit_meeting_date(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    logging.debug("Enter into callback_query_edit_meeting_date")
    assert context.matches is not None

    callback_data = guards.valid_date_callback_data(
        cb.EDIT_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DATE_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return None

    now_in_user_timezone = meeting.owner.now_in_tz()
    today_in_user_timezone = now_in_user_timezone.date()
    anchor_date = safe_anchor_date(meeting.datetime, now_in_user_timezone)

    current_date = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    meeting_date_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else None
    logging.debug(
        "Calendar view: "
        f"Anchor date: {anchor_date}, Current date: {current_date}, "
        f"Meeting date: {meeting_date_in_tz}, Now tz: {now_in_user_timezone} "
        f"Callback date: {callback_data.date}"
    )

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            lang=user.lang,
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
@with_async_session
async def callback_query_back_to_edit_datetime(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    logging.debug("Enter into callback_query_back_to_edit_datetime")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING.parse(context.match), EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK
    )

    user = guards.current_user(update, session)
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
    session: Session, context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
) -> ConversationMeetingState:
    meeting.datetime = dt.datetime.combine(cb_date, dt.time(0, 0, tzinfo=meeting.timezone)).astimezone(dt.UTC)
    session.add(meeting)
    session.flush()

    lang = meeting.lang
    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingMessages.EDIT_MEETING_TIME_ON_EXIT.get(lang=lang),
        cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
    )
    done_button = ButtonConfig(
        text=ButtonMessages.DONE.get(lang=lang),
        callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
    )
    assert meeting.datetime is not None
    datetime_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.datetime, "DT")
    view = MitupView(
        description=MeetingMessages.NEW_DATE_SET_SUCCESS.get(
            lang=lang,
            datetime=render(t"{datetime_entity}"),
        ),
        keyboard=[[done_button]],
    )
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    return ConversationMeetingState.EDIT_TIME


async def handle_datetime_update(
    session: Session, context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
) -> ConversationMeetingState:
    meeting.datetime = dt.datetime.combine(
        dt.date(cb_date.year, cb_date.month, cb_date.day),
        cast(dt.datetime, meeting.datetime).time(),
        tzinfo=dt.UTC,
    )
    session.add(meeting)
    session.flush()

    assert meeting.datetime is not None
    datetime_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.datetime, "DT")
    today = meeting.owner.now_in_tz().date()
    await context.api.edit_message(
        update=update,
        view=build_edit_datetime_entry_view(meeting, meeting.lang, today).with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(lang=meeting.lang, datetime=render(t"{datetime_entity}"))
        ),
    )
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    return ConversationMeetingState.EDIT_DATETIME


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.SET_DATE_CALLBACK, callback_data=cb.SET_MEETING_DATE)
@with_async_session
async def callback_query_set_meeting_date(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_set_meeting_date")

    callback_data = guards.valid_date_callback_data(
        cb.SET_MEETING_DATE.parse(context.match), EditMeetingHandlerId.SET_DATE_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return ConversationHandler.END

    if meeting.datetime is None:
        return await handle_first_datetime_set(session, context, update, meeting, callback_data.date)
    return await handle_datetime_update(session, context, update, meeting, callback_data.date)


# --- Time editing ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.EDIT_TIME_CALLBACK, callback_data=cb.EDIT_MEETING_TIME, bindable=False
)
@with_async_session
async def callback_query_set_meeting_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into callback_query_set_meeting_time")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_TIME.parse(context.match), EditMeetingHandlerId.EDIT_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
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
@with_async_session
async def date_time_entity_message_handler(session: Session, update: Update, context: TMitupContext) -> int:
    """Handle a message containing a ``date_time`` entity by setting it on the meeting and ending the conversation."""
    logging.debug("Enter into date_time_entity_message_handler")

    message = guards.message(update)
    entities = message.entities or []
    date_entity = next(e for e in entities if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_TIME) as meeting_id:
        current_user = guards.current_user(update, session)
        meeting = await guards.meeting_accessible(
            session, current_user, meeting_id, "Set datetime from entity", update, context
        )

        if meeting is None:
            return ConversationHandler.END

        meeting.datetime = unix_time
        session.add(meeting)
        session.flush()

        assert meeting.datetime is not None
        datetime_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.datetime, "DT")
        view = meeting.edit_view.with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(lang=current_user.lang, datetime=render(t"{datetime_entity}"))
        )

        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(session=session, meeting=meeting)

        return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_async_session
async def set_time_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    logging.debug("Enter into set_time_message_handler")

    time_info = cast(Match, context.match).groupdict()

    if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
        current_user = guards.current_user(update, session)
        await context.api.send_message(
            update=update,
            view=MeetingMessages.INVALID_TIME.get(lang=current_user.lang),
        )
        context.emit_metric(MetricKey.ERROR.with_prefix("InvalidTime"), 1)
        return ConversationMeetingState.EDIT_TIME

    with context.meeting_id(ContextId.EDIT_MEETING_TIME) as meeting_id:
        current_user = guards.current_user(update, session)

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=current_user.settings.tz)

        meeting = await guards.meeting_accessible(session, current_user, meeting_id, "Set time", update, context)

        if meeting is None:
            return ConversationHandler.END

        date_to_set = current_user.datetime_in_tz(meeting.datetime or dt.datetime.now(dt.UTC)).date()
        meeting.datetime = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        session.add(meeting)
        session.flush()

        assert meeting.datetime is not None
        datetime_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), meeting.datetime, "DT")
        view = meeting.edit_view.with_context(
            MeetingMessages.EDIT_TIME_SUCCESS.get(lang=current_user.lang, datetime=render(t"{datetime_entity}"))
        )

        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(session=session, meeting=meeting)

        return ConversationHandler.END


async def fallback_answer(session: Session, update: Update, context: TMitupContext) -> ConversationMeetingState:
    current_user = guards.current_user(update, session)

    await context.api.send_message(
        update=update,
        view=MeetingMessages.WRONG_TIME_FORMAT.get(lang=current_user.lang),
    )

    context.emit_metric(MetricKey.ERROR.with_prefix("WrongTimeFormat"), 1)

    return ConversationMeetingState.EDIT_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_FORMAT,
    bindable=False,
    filters=filters.TEXT & ~filters.COMMAND,
)
@with_async_session
async def wrong_message_sent_for_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    logging.debug("Enter into wrong_message_sent_for_time")

    return await fallback_answer(session, update, context)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_MESSAGE,
    bindable=False,
    filters=~filters.TEXT,
)
@with_async_session
async def wrong_message_type_sent_for_time(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    logging.debug("Enter into wrong_message_type_sent_for_time")

    return await fallback_answer(session, update, context)


async def datetime_state_fallback_answer(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    current_user = guards.current_user(update, session)

    await context.api.send_message(
        update=update,
        view=MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=current_user.lang, datetime_link=build_datetime_link()),
    )

    context.emit_metric(MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), 1)

    return ConversationMeetingState.EDIT_DATETIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
    bindable=False,
    filters=filters.TEXT & ~filters.COMMAND,
)
@with_async_session
async def datetime_wrong_text_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    logging.debug("Enter into datetime_wrong_text_message_handler")

    return await datetime_state_fallback_answer(session, update, context)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
    bindable=False,
    filters=~filters.TEXT,
)
@with_async_session
async def datetime_wrong_message_type_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    logging.debug("Enter into datetime_wrong_message_type_handler")

    return await datetime_state_fallback_answer(session, update, context)


# --- Conversation registration ---


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK, EditMeetingHandlerId.EDIT_TIME_CALLBACK],
    states={
        ConversationMeetingState.EDIT_DATETIME: [
            EditMeetingHandlerId.DATE_CALLBACK,
            EditMeetingHandlerId.EDIT_TIME_CALLBACK,
            EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK,
            EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK,
            EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK,
            EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
            EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
            EditMeetingHandlerId.BACK_TO_EDIT_MEETING_CALLBACK,
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
    fallbacks=[EditMeetingHandlerId.CANCEL],
)
