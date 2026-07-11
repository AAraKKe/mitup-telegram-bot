import datetime as dt

import pytest
from telegram import Update

from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingJoinMessages
from tests.helpers import MockDbSession, UpdateRequest, call_handler, create_meetup, create_user
from tests.helpers.handler_context import HandlerContext


def user_with_meeting(
    meeting_id: int = 1,
    end_datetime: dt.datetime | None = None,
    lock_on_start: bool = False,
    meeting_datetime: dt.datetime | None = None,
) -> tuple[User, Meetup]:
    meeting = create_meetup(id=meeting_id, title="Test Meeting", datetime=meeting_datetime)
    meeting.end_datetime = end_datetime
    meeting.lock_on_start = lock_on_start
    user = create_user(id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1))
    return user, meeting


def now_minus(minutes: int) -> dt.datetime:
    """Return a UTC datetime that is `minutes` ago."""
    return dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)


def now_plus(minutes: int) -> dt.datetime:
    """Return a UTC datetime that is `minutes` from now."""
    return dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Lock blocks join and leave when meeting is in progress
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(1)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(1)), MeetingHandlerId.LEAVE),
    ],
    ids=["join", "leave"],
    indirect=["update"],
)
async def test_join_leave_blocked_when_lock_on_start_and_in_progress(
    update: Update,
    handler_id: MeetingHandlerId,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # Meeting started 5 min ago, ends 85 min from now (90 min total) → is_in_progress
    user, meeting = user_with_meeting(
        meeting_id=1,
        end_datetime=now_plus(85),
        lock_on_start=True,
        meeting_datetime=now_minus(5),
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_LOCKED.get_text(lang=user.lang),
        show_alert=True,
    )

    # No join/leave side-effects
    context.api.assert_method_just_called("update_meeting_messages", times=0)
    assert len(meeting.joined_links) == 0


# ---------------------------------------------------------------------------
# Lock does NOT block when lock_on_start=False (meeting already in progress)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(1)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(1)), MeetingHandlerId.LEAVE),
    ],
    ids=["join", "leave"],
    indirect=["update"],
)
async def test_join_leave_allowed_when_lock_off_regardless_of_progress(
    update: Update,
    handler_id: MeetingHandlerId,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # Meeting is in progress (ends 85 min from now) but lock is off
    user, meeting = user_with_meeting(
        meeting_id=1,
        end_datetime=now_plus(85),
        lock_on_start=False,
        meeting_datetime=now_minus(5),
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    # Should NOT have produced the lock alert
    context.api.assert_method_just_called("answer_callback_query", times=1)
    # The answer was NOT the lock message
    call_kwargs = context.api.call_args("answer_callback_query").kwargs
    assert call_kwargs.get("show_alert") is False  # normal notification, not an alert


# ---------------------------------------------------------------------------
# Lock does NOT block when meeting has not started yet (future datetime)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(1)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(1)), MeetingHandlerId.LEAVE),
    ],
    ids=["join", "leave"],
    indirect=["update"],
)
async def test_join_leave_allowed_when_lock_on_but_meeting_not_started(
    update: Update,
    handler_id: MeetingHandlerId,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # Meeting starts in the future → not in progress
    user, meeting = user_with_meeting(
        meeting_id=1,
        end_datetime=now_plus(120),  # 90 min duration, both start and end in the future
        lock_on_start=True,
        meeting_datetime=now_plus(30),
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    # Should NOT have produced the lock alert
    context.api.assert_method_just_called("answer_callback_query", times=1)
    call_kwargs = context.api.call_args("answer_callback_query").kwargs
    assert call_kwargs.get("show_alert") is False  # normal notification


# ---------------------------------------------------------------------------
# Lock blocks join and leave with an open-ended window (start set, no end time)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(1)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(1)), MeetingHandlerId.LEAVE),
    ],
    ids=["join", "leave"],
    indirect=["update"],
)
async def test_join_leave_blocked_when_lock_on_and_open_ended_window(
    update: Update,
    handler_id: MeetingHandlerId,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # Start 5 min ago, no end time → open-ended window, is_in_progress from start onward
    user, meeting = user_with_meeting(
        meeting_id=1,
        end_datetime=None,
        lock_on_start=True,
        meeting_datetime=now_minus(5),
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_LOCKED.get_text(lang=user.lang),
        show_alert=True,
    )

    # No join/leave side-effects
    context.api.assert_method_just_called("update_meeting_messages", times=0)
    assert len(meeting.joined_links) == 0


# ---------------------------------------------------------------------------
# Lock does NOT block when there is no start time at all (is_in_progress is always False)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(1)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(1)), MeetingHandlerId.LEAVE),
    ],
    ids=["join", "leave"],
    indirect=["update"],
)
async def test_join_leave_allowed_when_lock_on_but_no_start_time(
    update: Update,
    handler_id: MeetingHandlerId,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # lock_on_start=True but no start time at all → is_in_progress is always False
    user, meeting = user_with_meeting(
        meeting_id=1,
        end_datetime=None,
        lock_on_start=True,
        meeting_datetime=None,
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    # Should NOT have produced the lock alert
    context.api.assert_method_just_called("answer_callback_query", times=1)
    call_kwargs = context.api.call_args("answer_callback_query").kwargs
    assert call_kwargs.get("show_alert") is False  # normal notification


# ---------------------------------------------------------------------------
# New meeting inherits lock_on_start from user.settings.default_lock_on_start
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("default_lock_on_start", [True, False], ids=["default_lock_true", "default_lock_false"])
@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="My New Meeting")],
    indirect=True,
)
async def test_create_meeting_inherits_lock_on_start_from_settings(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    default_lock_on_start: bool,
):
    settings = Settings(id=1, default_lock_on_start=default_lock_on_start)
    user = create_user(id=1, tg_user_id=123, settings=settings)
    mock_session.add_object(user, query_field="tg_user_id")

    await call_handler(MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, handler_context=handler_context)

    # The Meetup added to the session must inherit lock_on_start from the user's settings
    created_meetings = [obj for obj in mock_session.objects_added if isinstance(obj, Meetup)]
    assert len(created_meetings) == 1  # exactly one meetup created
    assert created_meetings[0].lock_on_start is default_lock_on_start  # inherited from settings
