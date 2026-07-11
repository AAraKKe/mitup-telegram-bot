import datetime as dt

import pytest
from freezegun import freeze_time
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.utils import safe_anchor_date
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingEditDurationMessages
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_user,
)
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID, DEFAULT_TEST_DATE, DEFAULT_TG_USER_PARAMS
from tests.helpers.monitoring import MetricAssertions


def date_time_entity_update(unix_dt: dt.datetime) -> Update:
    """Build an Update containing a message with a `date_time` entity."""
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
    """A fixed UTC start datetime in the future to use in tests.

    The now-relative past validation (validate_end_datetime) compares against
    wall-clock now, so tests freeze time before 2026-06-15 and use future dates.
    """
    return dt.datetime(2026, 6, 15, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def end_datetime() -> dt.datetime:
    """A fixed UTC end datetime 90 minutes after start_datetime()."""
    return dt.datetime(2026, 6, 15, 11, 30, tzinfo=dt.UTC)


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
        text=MeetingEditDurationMessages.END_STALE_ALERT.get_text(lang=user.lang),
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

    context.api.assert_edit_message_called(update, meeting_views.when_view(meeting))
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# End datetime validation: end <= start is rejected, stays in EDIT_END_DATETIME
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="09:00")],  # 09:00 UTC — before the 10:00 UTC start
    indirect=True,
)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_time_before_start_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """When the proposed end time is before the start datetime, an error is shown and state stays in EDIT_END_TIME.

    The handler derives the end date from meeting.end_datetime (if set). We pre-populate
    end_datetime with a placeholder on the same day as start so the time combination
    2026-06-15 09:00 UTC falls before start (2026-06-15 10:00 UTC). Now is frozen to 08:00 so
    09:00 is still in the future — this isolates the END_BEFORE_START path from END_IN_PAST.
    """
    # start = 2026-06-15 10:00 UTC; end placeholder is at midnight same day (will be replaced by user time input)
    start = start_datetime  # 2026-06-15 10:00 UTC
    end_placeholder = dt.datetime(2026, 6, 15, 0, 0, tzinfo=dt.UTC)  # same day, midnight

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
@freeze_time("2026-06-15 09:00:00", tz_offset=0)
async def test_valid_end_time_saves_end_datetime_and_exits_conversation(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    # No end_datetime, so the handler derives the date from now (frozen 2026-06-15 09:00);
    # 11:30 lands after both now and start (10:00) → valid.
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
    context.api.assert_update_meeting_messages_called(meeting=meeting)

    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DURATION_END_SET_DATE_CALLBACK — selecting an end date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 15)))],
    indirect=True,
)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
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

    # end_datetime should be set (23:59 in user TZ on 2026-06-15)
    assert meeting.end_datetime is not None
    assert state == ConversationMeetingState.EDIT_END_TIME
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()


@pytest.mark.parametrize(
    "update",
    # Pick a date BEFORE the start date (2026-06-15) so 23:59 on Jun 14 < start at 10:00 on Jun 15
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 14)))],
    indirect=True,
)
# Freeze before Jun 14 so the Jun 14 23:59 end is still in the future — isolates END_BEFORE_START
# from the END_IN_PAST check, which would otherwise take precedence.
@freeze_time("2026-06-13 12:00:00", tz_offset=0)
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
        text=MeetingEditDurationMessages.END_BEFORE_START.get_text(lang=user.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 16)))],
    indirect=True,
)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
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

    # UTC owner (no DST): changing only the date is a no-op on the local time-of-day, so the
    # existing 11:30 end time is kept and only the date moves to Jun 16.
    assert meeting.end_datetime == dt.datetime(2026, 6, 16, 11, 30, tzinfo=dt.UTC)
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    mock_session.assert_flushed()


@pytest.mark.parametrize(
    "update",
    # Move end date to Jun 14 — with existing end time 11:30 UTC, that's before start (Jun 15 10:00)
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 14)))],
    indirect=True,
)
# Freeze before Jun 14 so the Jun 14 11:30 end is still future — isolates END_BEFORE_START.
@freeze_time("2026-06-13 12:00:00", tz_offset=0)
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
        text=MeetingEditDurationMessages.END_BEFORE_START.get_text(lang=user.lang),
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# DURATION_END_DATETIME_ENTITY_MESSAGE — entity with end < start rejected
# ---------------------------------------------------------------------------


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_datetime_entity_before_start_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """A datetime entity whose unix_time is before the start datetime triggers a validation error.

    The entity time (start - 1s = 2026-06-15 09:59:59) is still in the future relative to the
    frozen now (08:00), so this isolates END_BEFORE_START from the END_IN_PAST check.
    """
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
# DURATION_END_ENTRY_CALLBACK — re-enter end datetime view via callback
# (lines 193-198)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(1))],
    indirect=True,
)
async def test_end_datetime_entry_callback_shows_end_datetime_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """DURATION_END_ENTRY_CALLBACK shows the end datetime entry view with edit_message."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK, handler_context=handler_context
    )

    assert state == ConversationMeetingState.EDIT_END_DATETIME
    # edit_message (not send_message) was used since use_send defaults to False
    context.api.assert_method_just_called("edit_message", times=1)
    context.api.assert_method_just_called("send_message", times=0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(999))],
    indirect=True,
)
async def test_end_datetime_entry_callback_meeting_not_accessible_returns_none(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """DURATION_END_ENTRY_CALLBACK returns None when meeting is not accessible."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK, handler_context=handler_context
    )

    # meeting_accessible returns None (id 999 != user's meeting id 1), so handler returns None
    assert state is None


# ---------------------------------------------------------------------------
# show_end_datetime_entry with use_send=True path (through save_end_datetime_and_finish)
# Line 265 — save_end_datetime via entity uses use_send=True
# ---------------------------------------------------------------------------


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_datetime_entity_valid_saves_and_sends(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """A datetime entity whose unix_time is after start saves end_datetime and exits via send_message."""
    after_start = start_datetime + dt.timedelta(hours=2)
    handler_context.update = date_time_entity_update(after_start)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    # end_datetime saved
    assert meeting.end_datetime == after_start

    # send_message was used (use_send=True path)
    context.api.assert_method_just_called("send_message", times=1)
    context.api.assert_update_meeting_messages_called(meeting=meeting)

    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# save_end_datetime_and_finish with use_send=False path (edit_message branch)
# Line 229 — triggered via set_end_date when end_datetime already exists, updating date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 16)))],
    indirect=True,
)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_update_existing_end_date_goes_back_to_entry_using_edit_message(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """When updating an existing end date, show_end_datetime_entry uses edit_message (use_send=False)."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    # Goes back to EDIT_END_DATETIME after setting the date
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    # edit_message was used (not send_message)
    context.api.assert_method_just_called("edit_message", times=1)
    context.api.assert_method_just_called("send_message", times=0)


# ---------------------------------------------------------------------------
# DURATION_END_WRONG_INPUT — wrong input in EDIT_END_DATETIME state
# (lines 280-286)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="random text here")],
    indirect=True,
)
async def test_duration_end_wrong_input_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    metrics: MetricAssertions,
):
    """Wrong input in EDIT_END_DATETIME state sends error message and stays in state."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
        handler_context=handler_context,
    )

    assert state == ConversationMeetingState.EDIT_END_DATETIME
    context.api.assert_method_just_called("send_message", times=1)
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongEndDatetimeFormat"), value=1)


# ---------------------------------------------------------------------------
# DURATION_END_DATE_NAV_CALLBACK — calendar navigation for end date
# (lines 309-327)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE.with_id(1).with_date(dt.date(2024, 6, 15)))],
    indirect=True,
)
async def test_duration_end_date_nav_shows_calendar(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """DURATION_END_DATE_NAV_CALLBACK shows a calendar view for end date selection."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK, handler_context=handler_context
    )

    assert state == ConversationMeetingState.EDIT_END_DATE

    # Use meeting.owner.now_in_tz().date() to get the same "today" the handler used; the callback
    # date (2024-06-15) is always in the past, so current_date resolves to today.
    now_in_owner_tz = meeting.owner.now_in_tz()
    today = now_in_owner_tz.date()
    anchor_date = safe_anchor_date(meeting.end_datetime, now_in_owner_tz)

    # Issue #173: this calendar's back button routes to the End Date & Time sub-hub, so it must
    # be labeled END_DATE_TIME — not the old hardcoded "≪ ✏️ Edit".
    context.api.assert_edit_message_called(
        update,
        factory.edit_meeting_date_view(
            RenderContext(lang=meeting.lang),
            meeting_id=1,
            anchor_date=anchor_date,
            current_date=today,
            new=meeting.end_datetime is None,
            set_date_callback=cb.SET_MEETING_END_DATE,
            nav_callback=cb.EDIT_MEETING_END_DATE,
            back_callback=cb.EDIT_MEETING_END_DATE_TIME,
            back_button_text=ButtonMessages.END_DATE_TIME,
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE.with_id(999).with_date(dt.date(2024, 6, 15)))],
    indirect=True,
)
async def test_duration_end_date_nav_meeting_not_accessible(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """DURATION_END_DATE_NAV_CALLBACK returns None when meeting is not accessible."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK, handler_context=handler_context
    )

    assert state is None


# ---------------------------------------------------------------------------
# DURATION_BACK_TO_END_DATETIME_CALLBACK — back button to end datetime entry
# (lines 345-350)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(1))],
    indirect=True,
)
async def test_back_to_end_datetime_shows_entry_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """DURATION_BACK_TO_END_DATETIME_CALLBACK re-shows the end datetime entry view."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK, handler_context=handler_context
    )

    assert state == ConversationMeetingState.EDIT_END_DATETIME
    context.api.assert_method_just_called("edit_message", times=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(999))],
    indirect=True,
)
async def test_back_to_end_datetime_meeting_not_accessible(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """DURATION_BACK_TO_END_DATETIME_CALLBACK returns None when meeting is not accessible."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK, handler_context=handler_context
    )

    assert state is None


# ---------------------------------------------------------------------------
# DURATION_END_TIME_CALLBACK — shows time input prompt for end time
# (lines 449-461)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_TIME.with_id(1))],
    indirect=True,
)
async def test_duration_end_time_callback_shows_time_prompt(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """DURATION_END_TIME_CALLBACK shows edit time prompt and returns EDIT_END_TIME state."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_TIME_CALLBACK, handler_context=handler_context
    )

    assert state == ConversationMeetingState.EDIT_END_TIME
    expected_view = MitupView(
        description=CommonMessages.TIME_PROMPT.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(1),
                )
            ]
        ],
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_END_TIME.with_id(999))],
    indirect=True,
)
async def test_duration_end_time_callback_meeting_not_accessible(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """DURATION_END_TIME_CALLBACK returns END when meeting is not accessible."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_TIME_CALLBACK, handler_context=handler_context
    )

    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DURATION_END_SET_TIME_MESSAGE — invalid time branch
# (lines 478-481)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="49:20")],
    indirect=True,
)
async def test_duration_end_set_time_invalid_time_shows_error(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
    metrics: MetricAssertions,
):
    """When an invalid time like 49:20 is entered for end time, an error is shown and state stays in EDIT_END_TIME."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert state == ConversationMeetingState.EDIT_END_TIME
    context.api.assert_send_message_called(update, CommonMessages.TIME_INVALID_VALUE.get(lang=user.lang))
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("InvalidTime"), value=1)


# ---------------------------------------------------------------------------
# DURATION_END_TIME_WRONG_INPUT — wrong time format for end time
# (lines 513-515)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="not a time")],
    indirect=True,
)
async def test_duration_end_time_wrong_input_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    metrics: MetricAssertions,
):
    """Wrong time format in EDIT_END_TIME state sends error message and stays in state."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
        handler_context=handler_context,
    )

    assert state == ConversationMeetingState.EDIT_END_TIME
    context.api.assert_send_message_called(update, CommonMessages.TIME_INVALID_FORMAT.get(lang=user.lang))
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongTimeFormat"), value=1)


# ---------------------------------------------------------------------------
# validate_end_datetime — now-relative past validation (END_IN_PAST)
#
# "now" is meeting.owner.now_in_tz().astimezone(UTC); the owner here uses UTC
# (Settings(id=1)), so frozen UTC time equals the comparison "now". The past
# check (<=) runs before the END_BEFORE_START check, so it takes precedence.
# ---------------------------------------------------------------------------


FROZEN_NOW = "2026-06-15 12:00:00"  # UTC
# A start in the future, so the only reason to reject the end is that it is in the past.
FUTURE_START = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.UTC)


@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_end_datetime_entity_in_past_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A datetime entity whose end is in the past returns END_IN_PAST and stays in EDIT_END_DATETIME."""
    # End is one hour before now (2026-06-15 11:00 UTC) but after a far-past start, so only the
    # past check applies.
    past_end = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)
    handler_context.update = date_time_entity_update(past_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=dt.datetime(2026, 6, 15, 10, 0, tzinfo=dt.UTC))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert meeting.end_datetime is None  # not saved
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    context.api.assert_send_message_called(
        handler_context.update, MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang)
    )


@pytest.mark.parametrize(
    "update",
    # 10:00 UTC end on the frozen day — before now (12:00), so in the past.
    [UpdateRequest(message_text="10:00")],
    indirect=True,
)
@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_end_time_in_past_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """An HH:MM end time that resolves to the past returns END_IN_PAST and stays in EDIT_END_TIME."""
    # Existing end_datetime carries the date used when combining the typed time, so 10:00 lands on
    # the frozen day (2026-06-15 10:00 UTC), two hours before now.
    user, meeting = owner_with_meeting(
        meeting_id=1,
        meeting_datetime=dt.datetime(2026, 6, 15, 8, 0, tzinfo=dt.UTC),
        end_datetime=dt.datetime(2026, 6, 15, 9, 0, tzinfo=dt.UTC),
    )
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)
    end_before = meeting.end_datetime

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert meeting.end_datetime == end_before  # unchanged
    assert state == ConversationMeetingState.EDIT_END_TIME
    context.api.assert_send_message_called(update, MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang))


@pytest.mark.parametrize(
    "update",
    # First end-date selection on a past day → 23:59 on that day is still before now.
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 14)))],
    indirect=True,
)
@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_set_end_date_in_past_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """Selecting a first end date in the past (23:59 < now) shows the END_IN_PAST alert."""
    # Owner is UTC, so 23:59 on Jun 14 is 2026-06-14 23:59 UTC, before now (Jun 15 12:00).
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=dt.datetime(2026, 6, 13, 10, 0, tzinfo=dt.UTC))
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
        text=MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang),
        show_alert=True,
    )


@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_end_in_past_takes_precedence_over_before_start(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """When the end is BOTH in the past AND before start, END_IN_PAST is reported, not END_BEFORE_START."""
    # End (Jun 15 11:00 UTC) is before start (Jun 15 20:00, future) AND before now (Jun 15 12:00).
    past_and_before_start = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)
    handler_context.update = date_time_entity_update(past_and_before_start)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=FUTURE_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert meeting.end_datetime is None
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    # END_IN_PAST wins over END_BEFORE_START because the past check runs first.
    context.api.assert_send_message_called(
        handler_context.update, MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang)
    )


@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_end_exactly_now_is_rejected(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """An end datetime exactly equal to now is rejected (comparison is <=, not <)."""
    exactly_now = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC)  # == frozen now
    handler_context.update = date_time_entity_update(exactly_now)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=dt.datetime(2026, 6, 15, 8, 0, tzinfo=dt.UTC))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert meeting.end_datetime is None
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    context.api.assert_send_message_called(
        handler_context.update, MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang)
    )


@freeze_time(FROZEN_NOW, tz_offset=0)
async def test_past_end_with_naive_stored_start_does_not_raise(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A naive stored meeting.datetime must not raise TypeError when comparing against the aware end."""
    # Stored start is naive (no tzinfo) — validate_end_datetime normalises it to aware UTC.
    naive_start = dt.datetime(2026, 6, 15, 8, 0)  # naive
    assert naive_start.tzinfo is None
    past_end = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)  # before now (12:00)
    handler_context.update = date_time_entity_update(past_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=naive_start)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 1},
    )

    assert meeting.end_datetime is None
    assert state == ConversationMeetingState.EDIT_END_DATETIME
    context.api.assert_send_message_called(
        handler_context.update, MeetingEditDurationMessages.END_IN_PAST.get_text(lang=user.lang)
    )


# ---------------------------------------------------------------------------
# callback_query_duration_end_set_date — "end already set" branch is DST-safe:
# changing only the date preserves the owner-LOCAL wall clock of the end time.
# ---------------------------------------------------------------------------


def madrid_owner_with_meeting(
    meeting_datetime: dt.datetime | None = None,
    end_datetime: dt.datetime | None = None,
) -> tuple[User, Meetup]:
    """Build a user in Europe/Madrid (a DST zone) owning a single meeting."""
    meeting = create_meetup(id=1, title="Test Meeting", datetime=meeting_datetime)
    meeting.end_datetime = end_datetime
    user = create_user(
        id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1, timezone="Europe/Madrid")
    )
    return user, meeting


@pytest.mark.parametrize(
    "update",
    # Move the end date across the spring DST boundary into summer time.
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 4, 10)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_update_existing_end_date_preserves_local_time_across_dst(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """DST regression: changing only the end date keeps the owner-LOCAL wall clock. The stored UTC
    value shifts by the offset delta; the displayed local end time does not move."""
    # Existing end: 2026-03-20 09:00 UTC == 10:00 Madrid (winter, UTC+1).
    start = dt.datetime(2026, 3, 20, 7, 0, tzinfo=dt.UTC)  # 08:00 Madrid, before the end
    existing_end = dt.datetime(2026, 3, 20, 9, 0, tzinfo=dt.UTC)
    user, meeting = madrid_owner_with_meeting(meeting_datetime=start, end_datetime=existing_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Local wall clock before the change is 10:00.
    assert user.datetime_in_tz(existing_end).time() == dt.time(10, 0)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    assert state == ConversationMeetingState.EDIT_END_DATETIME
    # 2026-04-10 is summer time (Madrid UTC+2), so 10:00 local == 08:00 UTC — the UTC time-of-day
    # shifted by one hour relative to the winter value (09:00 UTC).
    assert meeting.end_datetime == dt.datetime(2026, 4, 10, 8, 0, tzinfo=dt.UTC)
    # The owner-local wall clock is preserved at 10:00.
    assert meeting.end_datetime is not None
    assert user.datetime_in_tz(meeting.end_datetime).time() == dt.time(10, 0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_id(1).with_date(dt.date(2026, 6, 25)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_update_existing_end_date_with_naive_stored_datetime_treats_it_as_utc(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """A naive stored meeting.end_datetime is normalised via to_utc before the local-time
    preservation, so it is treated as UTC and not reinterpreted against the system timezone."""
    start = dt.datetime(2026, 6, 20, 8, 0, tzinfo=dt.UTC)
    # Naive end value (no tzinfo) — the fix tags it as UTC: 2026-06-20 10:00 UTC.
    naive_end = dt.datetime(2026, 6, 20, 10, 0)
    assert naive_end.tzinfo is None
    user, meeting = madrid_owner_with_meeting(meeting_datetime=start, end_datetime=naive_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        handler_context=handler_context,
    )

    assert state == ConversationMeetingState.EDIT_END_DATETIME
    # 10:00 UTC == 12:00 Madrid (summer, UTC+2); preserved on 2026-06-25 that local 12:00 == 10:00 UTC.
    assert meeting.end_datetime == dt.datetime(2026, 6, 25, 10, 0, tzinfo=dt.UTC)
