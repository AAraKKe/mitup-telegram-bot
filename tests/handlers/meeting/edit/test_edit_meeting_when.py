import datetime as dt

import pytest
from telegram import Update

from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views import factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_user,
)

START_DATETIME = dt.datetime(2024, 6, 15, 10, 0, tzinfo=dt.UTC)
END_DATETIME = dt.datetime(2024, 6, 15, 11, 30, tzinfo=dt.UTC)


def owner_with_meeting(
    meeting_id: int = 1,
    meeting_datetime: dt.datetime | None = START_DATETIME,
    end_datetime: dt.datetime | None = None,
    lock_on_start: bool = False,
):
    """Build a user owning a single meeting."""
    meeting = create_meetup(id=meeting_id, title="Test Meeting", datetime=meeting_datetime)
    meeting.end_datetime = end_datetime
    meeting.lock_on_start = lock_on_start
    user = create_user(id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1))
    return user, meeting


# ---------------------------------------------------------------------------
# WHEN_ENTRY_CALLBACK — renders when_view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_WHEN.with_id(1))],
    indirect=True,
)
@pytest.mark.parametrize(
    "meeting_datetime,end_datetime",
    [
        (START_DATETIME, END_DATETIME),
        (START_DATETIME, None),
        (None, None),
    ],
    ids=["with_both_times", "with_start_only", "without_times"],
)
async def test_when_entry_renders_when_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    meeting_datetime: dt.datetime | None,
    end_datetime: dt.datetime | None,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=meeting_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.WHEN_ENTRY_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting.when_view)


# ---------------------------------------------------------------------------
# CLEAR_TIMES_CALLBACK — shows confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DELETE_MEETING_TIMES.with_id(1))],
    indirect=True,
)
async def test_clear_times_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.datetime = START_DATETIME
    meeting.end_datetime = END_DATETIME
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.CLEAR_TIMES_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingMessages.CLEAR_TIMES_CONFIRMATION.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_TIMES.with_id(1),
        ),
    )


# ---------------------------------------------------------------------------
# CONFIRM_CLEAR_TIMES_CALLBACK — clears all 3 fields, shows when_view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(1))],
    indirect=True,
)
async def test_confirm_clear_times(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.datetime = START_DATETIME
    meeting.end_datetime = END_DATETIME
    meeting.lock_on_start = True
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK, handler_context=handler_context)

    assert meeting.datetime is None
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(
        update,
        meeting.when_view.with_context(MeetingMessages.TIMES_CLEARED.get(lang=user_with_settings.lang)),
    )
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


# ---------------------------------------------------------------------------
# DECLINE_CLEAR_TIMES_CALLBACK — no mutation, shows when_view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_TIMES.with_id(1))],
    indirect=True,
)
async def test_decline_clear_times(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.datetime = START_DATETIME
    meeting.end_datetime = END_DATETIME
    meeting.lock_on_start = True
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK, handler_context=handler_context)

    # No mutation
    assert meeting.datetime == START_DATETIME
    assert meeting.end_datetime == END_DATETIME
    assert meeting.lock_on_start is True
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    context.api.assert_edit_message_called(
        update,
        meeting.when_view.with_context(MeetingMessages.CLEAR_TIMES_DECLINE.get(lang=user_with_settings.lang)),
    )
    context.api.assert_update_meeting_messages_not_called()


# ---------------------------------------------------------------------------
# LOCK_ON_START_CALLBACK — toggle lock_on_start when end_datetime is set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
@pytest.mark.parametrize("initial_lock", [True, False], ids=["lock_true", "lock_false"])
async def test_lock_on_start_toggle(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    initial_lock: bool,
):
    user, meeting = owner_with_meeting(
        meeting_id=1, meeting_datetime=START_DATETIME, end_datetime=END_DATETIME, lock_on_start=initial_lock
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, handler_context=handler_context)

    assert meeting.lock_on_start == (not initial_lock)  # flipped

    context.api.assert_edit_message_called(update, meeting.when_view)
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),  # None -- no message registered
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
