import datetime as dt

import pytest
from telegram import Update

from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, MeetingEditWhenMessages
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
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
# WHEN_ENTRY_CALLBACK: stale submenu button, routed to the editor
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
async def test_when_entry_routes_to_the_editor(
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

    context.api.assert_edit_message_called(
        update,
        meeting_views.owner_view(meeting).with_context(CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user.lang)),
    )


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
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditWhenMessages.REMOVE_TIMES_CONFIRMATION.rich(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_TIMES.with_id(1),
        ),
    )


# ---------------------------------------------------------------------------
# CONFIRM_CLEAR_TIMES_CALLBACK: clears both times, keeps the lock setting, shows the editor
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
    # The lock is a standing setting: it survives the wipe, dormant until a new start time.
    assert meeting.lock_on_start is True

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


# ---------------------------------------------------------------------------
# DECLINE_CLEAR_TIMES_CALLBACK: no mutation, shows the editor
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

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_not_called()
