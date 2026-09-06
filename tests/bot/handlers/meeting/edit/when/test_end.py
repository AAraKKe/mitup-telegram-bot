import datetime as dt

import pytest
from freezegun import freeze_time
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.when import rules, screens
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingEditDateTimeMessages,
    MeetingEditDurationMessages,
    SupporterMessages,
)
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view
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


def expected_card(meeting: Meetup, lang: str, *, month: dt.date | None = None, **kwargs) -> MitupView:
    """The end datetime card the handler under test should have drawn, built the same way it does."""
    if month is None:
        month = rules.safe_anchor_date(meeting.end_datetime or meeting.datetime, meeting.owner.now_in_tz())
    return screens.end_datetime_card(meeting, lang, month=month, **kwargs)


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


@pytest.fixture(autouse=True)
def wide_scheduling_horizon(monkeypatch: pytest.MonkeyPatch):
    """Keep the date/time-mechanic tests focused on duration and ordering rather than the scheduling
    horizon: default to a horizon far beyond any date they pick. The dedicated horizon tests below
    narrow it via `narrow_horizon`."""
    monkeypatch.setattr(
        supporter.PolicyState,
        "config",
        LimitsConfig(free_scheduling_horizon_days=3650, patron_scheduling_horizon_days=3650),
    )


# ---------------------------------------------------------------------------
# Opening the card
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(1))], indirect=True)
async def test_open_end_editor_with_a_start_shows_the_card(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.OPEN_END_EDITOR, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert context.has_meeting_id(ContextId.EDIT_MEETING_END)
    context.api.assert_edit_message_called(update, expected_card(meeting, user.lang))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(1))], indirect=True)
async def test_open_end_editor_without_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.OPEN_END_EDITOR, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDurationMessages.END_STALE_ALERT.text(lang=user.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REOPEN_END_EDITOR.with_id(1))], indirect=True)
async def test_reopen_end_editor_shows_the_card(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.REOPEN_END_EDITOR, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_edit_message_called(update, expected_card(meeting, user.lang))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REOPEN_END_EDITOR.with_id(99))], indirect=True)
async def test_reopen_end_editor_meeting_not_accessible_ends_conversation(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, _ = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    not_owned = create_meetup(id=99, title="Not Owned", owner=create_user(id=2, tg_user_id=456))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(not_owned)

    context, state = await call_handler(EditMeetingHandlerId.REOPEN_END_EDITOR, handler_context=handler_context)

    assert state == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REOPEN_END_EDITOR.with_id(1))], indirect=True)
async def test_reopen_end_editor_without_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.REOPEN_END_EDITOR, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDurationMessages.END_STALE_ALERT.text(lang=user.lang),
        show_alert=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CANCEL_END_EDIT.with_id(1))], indirect=True)
async def test_cancel_end_edit_returns_to_the_editor(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.CANCEL_END_EDIT,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_END)
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))


# ---------------------------------------------------------------------------
# Month navigation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.NAVIGATE_END_CALENDAR.with_id(1).with_date(dt.date(2026, 7, 1)))],
    indirect=True,
)
@freeze_time("2026-06-01 12:00:00", tz_offset=0)
async def test_navigate_end_calendar_renders_the_requested_month(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.NAVIGATE_END_CALENDAR, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_edit_message_called(update, expected_card(meeting, user.lang, month=dt.date(2026, 7, 1)))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.NAVIGATE_END_CALENDAR.with_id(99).with_date(dt.date(2026, 7, 1)))],
    indirect=True,
)
async def test_navigate_end_calendar_meeting_not_accessible(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, _ = owner_with_meeting(meeting_id=1)
    not_owned = create_meetup(id=99, title="Not Owned", owner=create_user(id=2, tg_user_id=456))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(not_owned)

    context, state = await call_handler(EditMeetingHandlerId.NAVIGATE_END_CALENDAR, handler_context=handler_context)

    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# Picking a day
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 16)))],
    indirect=True,
)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_set_end_date_first_time_defaults_to_2359(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    # The owner is UTC, so 23:59 local is 23:59 UTC on the picked day.
    assert meeting.end_datetime == dt.datetime(2026, 6, 16, 23, 59, tzinfo=dt.UTC)
    context.api.assert_edit_message_called(update, expected_card(meeting, user.lang, month=dt.date(2026, 6, 16)))
    context.api.assert_update_meeting_messages_called(meeting, None, True)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 10)))],
    indirect=True,
)
@freeze_time("2026-06-01 08:00:00", tz_offset=0)
async def test_set_end_date_before_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime is None
    mock_session.assert_not_flushed()
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDurationMessages.END_BEFORE_START.text(lang=user.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 16)))],
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
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    # The UTC owner keeps the 11:30 wall clock on the new day.
    assert meeting.end_datetime == dt.datetime(2026, 6, 16, 11, 30, tzinfo=dt.UTC)
    context.api.assert_edit_message_called(update, expected_card(meeting, user.lang, month=dt.date(2026, 6, 16)))
    context.api.assert_update_meeting_messages_called(meeting, None, True)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 10)))],
    indirect=True,
)
@freeze_time("2026-06-01 08:00:00", tz_offset=0)
async def test_update_existing_end_date_before_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime == end_datetime  # unchanged
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDurationMessages.END_BEFORE_START.text(lang=user.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 16)))],
    indirect=True,
)
async def test_pick_end_date_without_start_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """A stale day tap after the start was cleared refuses instead of saving an end with no span."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationHandler.END
    assert meeting.end_datetime is None
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDurationMessages.END_STALE_ALERT.text(lang=user.lang),
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# The time row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.OPEN_END_TIME_PROMPT.with_id(1))], indirect=True)
async def test_open_end_time_prompt_sends_the_prompt(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.OPEN_END_TIME_PROMPT, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert context.has_meeting_id(ContextId.EDIT_MEETING_END)
    # The prompt replaces the card in place, so no interactive card lingers above the fresh one
    # the typed answer earns.
    context.api.assert_edit_message_called(
        update, screens.time_prompt_view(meeting.lang, cb.REOPEN_END_EDITOR.with_id(1))
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.OPEN_END_TIME_PROMPT.with_id(99))], indirect=True)
async def test_open_end_time_prompt_meeting_not_accessible(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    user, _ = owner_with_meeting(meeting_id=1)
    not_owned = create_meetup(id=99, title="Not Owned", owner=create_user(id=2, tg_user_id=456))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(not_owned)

    context, state = await call_handler(EditMeetingHandlerId.OPEN_END_TIME_PROMPT, handler_context=handler_context)

    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_END)


# ---------------------------------------------------------------------------
# Typed time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(message_text="14:00")], indirect=True)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_valid_end_time_saves_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    # 14:00 for the UTC owner on the end's day.
    assert meeting.end_datetime == dt.datetime(2026, 6, 15, 14, 0, tzinfo=dt.UTC)
    # The conversation stays on the card, so the meeting id survives for the next typed input.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_END)
    context.api.assert_send_message_called(update, expected_card(meeting, user.lang))
    context.api.assert_update_meeting_messages_called(meeting=meeting)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="14:00")], indirect=True)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_time_without_an_end_date_is_refused(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """A time is a property of a scheduled day: with no end date set, HH:MM answers with the
    select-a-date-first error instead of inventing a day."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime is None
    mock_session.assert_not_flushed()
    context.api.assert_send_message_called(
        update,
        expected_card(meeting, user.lang, error=MeetingEditDateTimeMessages.TIME_NEEDS_DATE.rich(lang=user.lang)),
    )


@pytest.mark.parametrize("update", [UpdateRequest(message_text="49:20")], indirect=True)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_type_end_time_invalid_value_shows_error(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime == end_datetime  # unchanged
    context.api.assert_send_message_called(
        update,
        expected_card(meeting, user.lang, error=CommonMessages.TIME_INVALID_VALUE.rich(lang=user.lang)),
    )


@pytest.mark.parametrize("update", [UpdateRequest(message_text="09:00")], indirect=True)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_time_before_start_shows_error_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
):
    """A typed time producing an end at or before the start answers with END_BEFORE_START."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime, end_datetime=end_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime == end_datetime  # unchanged
    context.api.assert_send_message_called(
        update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_BEFORE_START.rich(lang=user.lang)),
    )


@pytest.mark.parametrize("update", [UpdateRequest(message_text="14:00")], indirect=True)
async def test_end_time_with_start_removed_recovers_to_the_editor(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """Typed end input after the start was cleared lands on the editor with the stale notice."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_END)
    context.api.assert_send_message_called(
        update,
        meeting_views.owner_view(meeting).with_context(
            MeetingEditDurationMessages.END_STALE_ALERT.rich(lang=user.lang)
        ),
    )


# ---------------------------------------------------------------------------
# Typed datetime entity
# ---------------------------------------------------------------------------


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_datetime_entity_valid_saves_and_stays_on_the_card(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    proposed_end = dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC)
    handler_context.update = date_time_entity_update(proposed_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime == proposed_end
    context.api.assert_send_message_called(handler_context.update, expected_card(meeting, user.lang))
    context.api.assert_update_meeting_messages_called(meeting=meeting)


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_datetime_entity_before_start_shows_error_and_stays_on_the_card(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    before_start = dt.datetime(2026, 6, 15, 9, 0, tzinfo=dt.UTC)
    handler_context.update = date_time_entity_update(before_start)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime is None
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_BEFORE_START.rich(lang=user.lang)),
    )


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_datetime_entity_with_start_removed_recovers_to_the_editor(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    handler_context.update = date_time_entity_update(dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC))

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        handler_context.update,
        meeting_views.owner_view(meeting).with_context(
            MeetingEditDurationMessages.END_STALE_ALERT.rich(lang=user.lang)
        ),
    )


# ---------------------------------------------------------------------------
# Input the screen cannot use
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(message_text="not a time")], indirect=True)
@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_reject_end_datetime_shows_error_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.REJECT_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user.lang,
            error=CommonMessages.DATETIME_INPUT_INVALID.rich(
                lang=user.lang, datetime_link=screens.datetime_link_content()
            ),
        ),
    )


# ---------------------------------------------------------------------------
# validate_end_datetime: past and ordering
# ---------------------------------------------------------------------------


@freeze_time("2026-06-15 12:00:00", tz_offset=0)
async def test_set_end_date_in_past_shows_alert(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A past end is rejected before any other check."""
    past_end = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)
    handler_context.update = date_time_entity_update(past_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=dt.datetime(2026, 6, 15, 8, 0, tzinfo=dt.UTC))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime is None
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_IN_PAST.rich(lang=user.lang)),
    )


@freeze_time("2026-06-15 12:00:00", tz_offset=0)
async def test_end_in_past_takes_precedence_over_before_start(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """An end both past and before the start reports the past: the more fundamental problem."""
    past_end = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)  # before now AND before the future start
    handler_context.update = date_time_entity_update(past_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_IN_PAST.rich(lang=user.lang)),
    )


@freeze_time("2026-06-15 12:00:00", tz_offset=0)
async def test_end_exactly_now_is_rejected(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    start_datetime: dt.datetime,
):
    """An end exactly at now is rejected (comparison is <=, not <)."""
    handler_context.update = date_time_entity_update(dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC))

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=start_datetime)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert state == ConversationMeetingState.END_DATETIME_CARD
    assert meeting.end_datetime is None
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_IN_PAST.rich(lang=user.lang)),
    )


@freeze_time("2026-06-15 12:00:00", tz_offset=0)
async def test_past_end_with_naive_stored_start_does_not_raise(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A naive stored meeting.datetime must not raise TypeError when comparing against the aware end."""
    naive_start = dt.datetime(2026, 6, 15, 8, 0)  # naive
    assert naive_start.tzinfo is None
    past_end = dt.datetime(2026, 6, 15, 11, 0, tzinfo=dt.UTC)  # before now (12:00)
    handler_context.update = date_time_entity_update(past_end)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=naive_start)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime is None
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_IN_PAST.rich(lang=user.lang)),
    )


# ---------------------------------------------------------------------------
# callback_query_pick_end_date — "end already set" branch is DST-safe:
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
    # Move the end date across the spring DST boundary (Madrid springs forward 2026-03-29), staying
    # within the one-week duration cap relative to the Mar 26 start.
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 4, 1)))],
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
    # Existing end: 2026-03-26 09:00 UTC == 10:00 Madrid (winter, UTC+1).
    start = dt.datetime(2026, 3, 26, 7, 0, tzinfo=dt.UTC)  # 08:00 Madrid, before the end
    existing_end = dt.datetime(2026, 3, 26, 9, 0, tzinfo=dt.UTC)
    user, meeting = madrid_owner_with_meeting(meeting_datetime=start, end_datetime=existing_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Local wall clock before the change is 10:00.
    assert user.datetime_in_tz(existing_end).time() == dt.time(10, 0)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    # 2026-04-01 is summer time (Madrid UTC+2), so 10:00 local == 08:00 UTC — the UTC time-of-day
    # shifted by one hour relative to the winter value (09:00 UTC).
    assert meeting.end_datetime == dt.datetime(2026, 4, 1, 8, 0, tzinfo=dt.UTC)
    # The owner-local wall clock is preserved at 10:00.
    assert meeting.end_datetime is not None
    assert user.datetime_in_tz(meeting.end_datetime).time() == dt.time(10, 0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 25)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_update_existing_end_date_with_naive_stored_datetime_treats_it_as_utc(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """A naive stored meeting.end_datetime is read as UTC before the local-time preservation, so it
    is not reinterpreted against the system timezone."""
    start = dt.datetime(2026, 6, 20, 8, 0, tzinfo=dt.UTC)
    # A naive end value reads as 2026-06-20 10:00 UTC.
    naive_end = dt.datetime(2026, 6, 20, 10, 0)
    assert naive_end.tzinfo is None
    user, meeting = madrid_owner_with_meeting(meeting_datetime=start, end_datetime=naive_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.PICK_END_DATE, handler_context=handler_context)

    assert state == ConversationMeetingState.END_DATETIME_CARD
    # 10:00 UTC == 12:00 Madrid (summer, UTC+2); preserved on 2026-06-25 that local 12:00 == 10:00 UTC.
    assert meeting.end_datetime == dt.datetime(2026, 6, 25, 10, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Maximum-duration cap: end - start must be at most one week. No tier lifts it.
# The owner is UTC (Settings(id=1)) and the default 90-day horizon is wide enough
# that only the duration cap can reject these ends.
# ---------------------------------------------------------------------------


DURATION_CAP_START = dt.datetime(2026, 6, 15, 10, 0, tzinfo=dt.UTC)


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_exactly_one_week_after_start_is_accepted(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """An end exactly one week after the start is within the cap and saves."""
    one_week_after = DURATION_CAP_START + dt.timedelta(days=7)  # 2026-06-22 10:00 UTC
    handler_context.update = date_time_entity_update(one_week_after)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=DURATION_CAP_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime == one_week_after
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_update_meeting_messages_called(meeting=meeting)


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_end_one_minute_beyond_one_week_is_rejected(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """An end one minute past the one-week cap is rejected and the meeting is unchanged."""
    beyond_cap = DURATION_CAP_START + dt.timedelta(days=7, minutes=1)  # 2026-06-22 10:01 UTC
    handler_context.update = date_time_entity_update(beyond_cap)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=DURATION_CAP_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime is None  # not saved
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_MAX_DURATION.rich(lang=user.lang)),
    )


@freeze_time("2026-06-15 08:00:00", tz_offset=0)
async def test_organizer_still_hits_the_one_week_duration_cap(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """The Organizer tier has an unlimited horizon but the one-week cap still applies to it."""
    eight_days_after = DURATION_CAP_START + dt.timedelta(days=8)  # 2026-06-23 10:00 UTC
    handler_context.update = date_time_entity_update(eight_days_after)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=DURATION_CAP_START)
    user.supporter_level = SupporterLevel.HOST_3
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime is None  # not saved
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(
        handler_context.update,
        expected_card(meeting, user.lang, error=MeetingEditDurationMessages.END_MAX_DURATION.rich(lang=user.lang)),
    )


# ---------------------------------------------------------------------------
# Scheduling horizon on the end: an end within the duration cap but beyond the
# owner's horizon is rejected with the Collaborate upsell, mirroring the start
# flow. The horizon here is narrowed to 5 days; the start sits at "now" so it is
# always within horizon, isolating the end check.
# ---------------------------------------------------------------------------


HORIZON_FROZEN_NOW = "2026-06-15 12:00:00"  # UTC; horizon boundary is 2026-06-20
HORIZON_START = dt.datetime(2026, 6, 15, 13, 0, tzinfo=dt.UTC)


@pytest.fixture
def narrow_horizon(monkeypatch: pytest.MonkeyPatch, wide_scheduling_horizon: None):
    """Narrow the free scheduling horizon to 5 days for the end-horizon tests.

    Depends on `wide_scheduling_horizon` so it runs after that autouse fixture and its narrower
    config wins.
    """
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=5))


def end_horizon_upsell_view(user: User) -> MitupView:
    """The upsell view sent as a reply when an end is beyond the free horizon."""
    back_button = ButtonConfig(
        text=ButtonMessages.END_DATE_TIME.back(lang=user.lang),
        callback_data=cb.REOPEN_END_EDITOR.with_id(1),
    )
    return supporter_upsell_view(
        SupporterMessages.SCHEDULING_HORIZON.rich(lang=user.lang, days=5),
        user.lang,
    ).with_context_menu([[back_button]])


@pytest.mark.parametrize(
    "update",
    # First end-date pick on Jun 21: within the 7-day cap (6 days out) but past the 5-day horizon.
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 21)))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_end_date_beyond_horizon_shows_upsell_in_place(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    narrow_horizon: None,
):
    """A calendar end date beyond the horizon edits the message into the upsell and stays on the card."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=HORIZON_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.PICK_END_DATE,
        handler_context=handler_context,
    )

    assert meeting.end_datetime is None  # not saved
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_method_just_called("answer_callback_query", times=0)
    calendar_button = ButtonConfig(
        text=ButtonMessages.END_DATE_TIME.back(lang=user.lang),
        callback_data=cb.NAVIGATE_END_CALENDAR.with_id(1).with_date(dt.date(2026, 6, 15)),
    )
    context.api.assert_edit_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON.rich(lang=user.lang, days=5),
            user.lang,
        ).with_context_menu([[calendar_button]]),
    )


@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_end_datetime_entity_beyond_horizon_sends_upsell(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    narrow_horizon: None,
):
    """A sent end entity beyond the horizon replies with the upsell and stays on the card."""
    beyond_horizon = dt.datetime(2026, 6, 21, 12, 0, tzinfo=dt.UTC)  # 6 days out, past the 5-day horizon
    handler_context.update = date_time_entity_update(beyond_horizon)

    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=HORIZON_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime is None  # not saved
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(handler_context.update, end_horizon_upsell_view(user))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="14:00")],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_end_time_edit_keeping_beyond_horizon_date_sends_upsell(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    narrow_horizon: None,
):
    """Unlike the start flow, an HH:MM end edit is not exempt from the horizon: re-timing a
    grandfathered beyond-horizon end still gets the upsell and does not save."""
    # Existing end date is Jun 21, past the 5-day horizon; the typed time keeps that date.
    grandfathered_end = dt.datetime(2026, 6, 21, 10, 0, tzinfo=dt.UTC)
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=HORIZON_START, end_datetime=grandfathered_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TYPE_END_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_END: 1},
    )

    assert meeting.end_datetime == grandfathered_end  # unchanged
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_send_message_called(update, end_horizon_upsell_view(user))


@pytest.mark.parametrize(
    "update",
    # First end-date pick exactly on the horizon boundary (Jun 20): accepted.
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 20)))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_end_date_exactly_on_horizon_is_accepted(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    narrow_horizon: None,
):
    """The horizon boundary itself is allowed: the end date saves and the card shows it."""
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=HORIZON_START)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.PICK_END_DATE,
        handler_context=handler_context,
    )

    assert meeting.end_datetime is not None  # saved (23:59 on the boundary date)
    assert user.datetime_in_tz(meeting.end_datetime).date() == dt.date(2026, 6, 20)
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "update",
    # Move a grandfathered far-future end onto Jun 18, within both the cap and the 5-day horizon.
    [UpdateRequest(callback_query=cb.PICK_END_DATE.with_id(1).with_date(dt.date(2026, 6, 18)))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_grandfathered_far_future_end_can_be_moved_to_a_valid_nearer_date(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    narrow_horizon: None,
):
    """Grandfathering: an existing end far beyond the horizon can be edited to a nearer valid date.

    Only the newly proposed end value is validated, so pulling the end back inside the cap and
    horizon succeeds even though the meeting was previously out of bounds.
    """
    far_future_end = dt.datetime(2026, 9, 1, 15, 0, tzinfo=dt.UTC)  # beyond both horizon and cap
    user, meeting = owner_with_meeting(meeting_id=1, meeting_datetime=HORIZON_START, end_datetime=far_future_end)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.PICK_END_DATE,
        handler_context=handler_context,
    )

    # UTC owner: updating only the date preserves the 15:00 local time-of-day on Jun 18.
    assert meeting.end_datetime == dt.datetime(2026, 6, 18, 15, 0, tzinfo=dt.UTC)
    assert state == ConversationMeetingState.END_DATETIME_CARD
    context.api.assert_method_just_called("answer_callback_query", times=0)
