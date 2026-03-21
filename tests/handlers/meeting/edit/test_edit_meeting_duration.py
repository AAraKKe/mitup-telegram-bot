import datetime as dt

import pytest
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.edit_meeting_duration import build_start_datetime_entry_view
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
# DURATION_ENTRY_CALLBACK — renders duration_view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_duration_entry_renders_duration_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.DURATION_ENTRY_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting.duration_view)


# ---------------------------------------------------------------------------
# DURATION_INPUT_CALLBACK — conversation entry, auto-chain:
#   meeting.datetime is None → DURATION_SET_START_DATETIME state
#   meeting.datetime is set → EDIT_END_DATETIME state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_duration_input_entry_without_start_datetime_enters_start_datetime_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When meeting has no start datetime, the conversation starts with DURATION_SET_START_DATETIME."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.DURATION_INPUT_CALLBACK, handler_context=handler_context)

    # Shows the start datetime entry prompt (date/time buttons + cancel)
    expected_view = build_start_datetime_entry_view(1, user.lang, user.now_in_tz().date())
    context.api.assert_edit_message_called(update, expected_view)
    assert state == ConversationMeetingState.DURATION_SET_START_DATETIME  # auto-chain to start datetime


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DURATION.with_id(1))],
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


# ---------------------------------------------------------------------------
# DURATION_CANCEL_CALLBACK — cancel returns to duration_view, exits conversation
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

    context.api.assert_edit_message_called(update, meeting.duration_view)
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DURATION_CLEAR_CALLBACK — clear sets end_datetime=None and lock_on_start=False
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CLEAR_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_clear_duration_removes_end_datetime_and_lock(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(
        meeting_id=1, end_datetime=end_datetime, meeting_datetime=start_datetime, lock_on_start=True
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.DURATION_CLEAR_CALLBACK, handler_context=handler_context)

    assert meeting.end_datetime is None  # cleared
    assert meeting.lock_on_start is False  # also cleared

    expected_view = meeting.duration_view.with_context(MeetingMessages.DURATION_CLEARED.get(lang=user.lang))
    context.api.assert_send_message_called(update, expected_view)
    context.api.assert_update_meeting_messages_called(session=mock_session, meeting=meeting)


# ---------------------------------------------------------------------------
# LOCK_ON_START_CALLBACK — toggle lock_on_start when end_datetime is set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
@pytest.mark.parametrize("initial_lock", [True, False], ids=["lock_true", "lock_false"])
async def test_toggle_lock_on_start_flips_value_and_re_renders(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    initial_lock: bool,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(
        meeting_id=1, end_datetime=end_datetime, meeting_datetime=start_datetime, lock_on_start=initial_lock
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, handler_context=handler_context)

    assert meeting.lock_on_start == (not initial_lock)  # flipped

    context.api.assert_edit_message_called(update, meeting.duration_view)
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),  # None — no message registered
        skip_current=True,
    )


# ---------------------------------------------------------------------------
# LOCK_ON_START_CALLBACK — stale callback when end_datetime is None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
async def test_lock_on_start_stale_alert_when_no_end_datetime(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, meeting = owner_with_meeting(meeting_id=1, end_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    original_lock = meeting.lock_on_start

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, handler_context=handler_context)

    # lock_on_start must not have been modified
    assert meeting.lock_on_start == original_lock

    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.LOCK_ON_START_STALE_ALERT.get_text(lang=meeting.user_language),
        show_alert=True,
    )

    # No message edit should have occurred
    context.api.assert_method_just_called("edit_message", times=0)


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


# ---------------------------------------------------------------------------
# DURATION_START_DATETIME_ENTITY_MESSAGE — entity sets start and transitions to EDIT_END_DATETIME
# ---------------------------------------------------------------------------


async def test_start_datetime_entity_sets_start_and_transitions_to_end_datetime(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """A datetime entity in DURATION_SET_START_DATETIME sets meeting.datetime and enters EDIT_END_DATETIME."""
    start = start_datetime
    handler_context.update = date_time_entity_update(start)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_START_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_DURATION: 1},
    )

    # meeting.datetime has been set from the entity
    assert meeting.datetime == start

    # Handler transitions to EDIT_END_DATETIME
    assert state == ConversationMeetingState.EDIT_END_DATETIME
