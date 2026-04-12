import datetime as dt

import pytest
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Settings
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_user,
)
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID, DEFAULT_TEST_DATE, DEFAULT_TG_USER_PARAMS


def date_time_entity_update(unix_dt: dt.datetime) -> Update:
    """Build an Update containing a message with a ``date_time`` entity."""
    tg_user = TgUser(**DEFAULT_TG_USER_PARAMS)
    chat = Chat(id=DEFAULT_CHAT_ID, type="private")
    text = "Tomorrow at noon"
    entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=len(text), unix_time=unix_dt)
    message = Message(
        DEFAULT_MESSAGE_ID,
        date=DEFAULT_TEST_DATE,
        chat=chat,
        from_user=tg_user,
        text=text,
        entities=[entity],
    )
    return Update(DEFAULT_MESSAGE_ID, message=message)


def owner_with_meeting(
    meeting_id: int = 1,
    end_datetime: dt.datetime | None = None,
    meeting_datetime: dt.datetime | None = None,
    lock_on_start: bool = False,
):
    """Build a user owning a single meeting."""
    meeting = create_meetup(id=meeting_id, title="Test Meeting", datetime=meeting_datetime)
    meeting.end_datetime = end_datetime
    meeting.lock_on_start = lock_on_start
    user = create_user(id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1))
    return user, meeting


@pytest.fixture
def start_datetime() -> dt.datetime:
    """A fixed UTC start datetime in the past to use in tests."""
    return dt.datetime(2024, 6, 15, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def end_datetime() -> dt.datetime:
    """A fixed UTC end datetime 90 minutes after start_datetime()."""
    return dt.datetime(2024, 6, 15, 11, 30, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# DURATION_INPUT_CALLBACK — conversation entry:
#   meeting.datetime is None → stale alert + END
#   meeting.datetime is set → EDIT_END_DATETIME state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_TIME.with_id(1))],
    indirect=True,
)
async def test_duration_input_entry_with_start_datetime_enters_end_datetime_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """When meeting already has a start datetime, the conversation skips to EDIT_END_DATETIME."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.DURATION_INPUT_CALLBACK, handler_context=handler_context)

    assert state == ConversationMeetingState.EDIT_END_DATETIME


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_TIME.with_id(1))],
    indirect=True,
)
async def test_set_end_time_without_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When meeting has no start datetime, the handler shows a stale alert and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.DURATION_INPUT_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.SET_END_TIME_STALE_ALERT.get_text(lang=user.lang),
        show_alert=True,
    )
    # No message edit should have occurred
    context.api.assert_method_just_called("edit_message", times=0)


# ---------------------------------------------------------------------------
# DURATION_CANCEL_CALLBACK — cancel returns to when_view, exits conversation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_cancel_during_duration_input_returns_to_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, end_datetime=end_datetime, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_DURATION: 1},
    )

    # end_datetime must not have changed
    assert meeting.end_datetime == end_datetime  # unchanged

    context.api.assert_edit_message_called(update, meeting.when_view)
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# End datetime validation: end <= start is rejected, stays in EDIT_END_DATETIME
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="09:00")],  # 09:00 UTC — before the 10:00 UTC start
    indirect=True,
)
async def test_end_time_before_start_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """When the proposed end time is before the start datetime, an error is shown and state stays in EDIT_END_TIME.

    The handler derives the end date from meeting.end_datetime (if set). We pre-populate
    end_datetime with a placeholder on the same day as start so the time combination
    2024-06-15 09:00 UTC falls before start (2024-06-15 10:00 UTC).
    """
    # start = 2024-06-15 10:00 UTC; end placeholder is at midnight same day (will be replaced by user time input)
    start = start_datetime  # 2024-06-15 10:00 UTC
    end_placeholder = dt.datetime(2024, 6, 15, 0, 0, tzinfo=dt.UTC)  # same day, midnight

    user, meeting = owner_with_meeting(
        meeting_id=1,
        meeting_datetime=start,
        end_datetime=end_placeholder,  # handler uses this date when combining with input time
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    # end_datetime must NOT have been updated — proposed 09:00 is before start 10:00
    assert meeting.end_datetime == end_placeholder  # unchanged

    # Error message was sent
    context.api.assert_method_just_called("send_message", times=1)
    # Conversation stays in EDIT_END_TIME state
    assert state == ConversationMeetingState.EDIT_END_TIME


# ---------------------------------------------------------------------------
# DURATION_END_SET_TIME_MESSAGE — valid end time saves end_datetime and exits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="11:30")],  # 90 min after start (10:00 UTC)
    indirect=True,
)
async def test_valid_end_time_saves_end_datetime_and_exits_conversation(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    # end_datetime must have been saved (the exact value depends on user TZ, check it is set)
    assert meeting.end_datetime is not None

    # Success response sent and meeting messages updated
    context.api.assert_method_just_called("send_message", times=1)
    context.api.assert_update_meeting_messages_called(session=mock_session, meeting=meeting)

    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DURATION_END_SET_DATE_CALLBACK — selecting an end date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2024, 6, 15)))],
    indirect=True,
)
async def test_set_end_date_first_time_defaults_to_2359(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """First end-date selection defaults to 23:59 in user TZ, saves, and prompts for time."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    # end_datetime should be set (23:59 in user TZ on 2024-06-15)
    assert meeting.end_datetime is not None
    assert state == ConversationMeetingState.EDIT_END_TIME
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()


@pytest.mark.parametrize(
    "update",
    # Pick a date BEFORE the start date (2024-06-15) so 23:59 on Jun 14 < start at 10:00 on Jun 15
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2024, 6, 14)))],
    indirect=True,
)
async def test_set_end_date_before_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """Selecting an end date where even 23:59 is before start triggers an alert."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    assert meeting.end_datetime is None
    assert state == ConversationMeetingState.EDIT_END_DATE
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.END_DATETIME_BEFORE_START.get_text(lang=user.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2024, 6, 16)))],
    indirect=True,
)
async def test_update_existing_end_date_valid(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """Updating an existing end date preserves the existing end time on the new date."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    # Date changed to Jun 16, time kept from original end_datetime (11:30 UTC)
    assert meeting.end_datetime is not None
    assert meeting.end_datetime.date() == dt.date(2024, 6, 16)
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    mock_session.assert_flushed()


@pytest.mark.parametrize(
    "update",
    # Move end date to Jun 14 — with existing end time 11:30 UTC, that's before start (Jun 15 10:00)
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2024, 6, 14)))],
    indirect=True,
)
async def test_update_existing_end_date_before_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """Updating end date to a date where end would be before start shows an alert."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    # end_datetime should NOT have changed
    assert meeting.end_datetime == end_datetime
    assert state == ConversationMeetingState.EDIT_END_DATE
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.END_DATETIME_BEFORE_START.get_text(lang=user.lang),
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# DURATION_END_DATETIME_ENTITY_MESSAGE — entity with end < start rejected
# ---------------------------------------------------------------------------


async def test_end_datetime_entity_before_start_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """A datetime entity whose unix_time is before the start datetime triggers a validation error."""
    # The entity unix_time is 1 second before the start datetime
    before_start = start_datetime - dt.timedelta(seconds=1)
    handler_context.update = date_time_entity_update(before_start)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    # end_datetime must NOT have been saved
    assert meeting.end_datetime is None

    context.api.assert_method_just_called("send_message", times=1)
    assert state == ConversationMeetingState.EDIT_END_DATETIME
