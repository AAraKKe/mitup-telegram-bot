import datetime as dt
from typing import cast

import pytest
from freezegun import freeze_time
from telegram import CallbackQuery, Location, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.when import screens
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MeetupMessage
from mitup_bot.monitoring import Feature, MetricKey, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingEditDateTimeMessages,
    SupporterMessages,
)
from mitup_bot.utils.rich_message import datetime_link_content
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_member,
    make_test_metrics_client,
)
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

TEST_MEETING_DATETIME_UTC = dt.datetime(2024, 12, 21, 12, 0, tzinfo=dt.UTC)
TEST_CURRENT_DATE = dt.date(2024, 11, 15)


@pytest.fixture(autouse=True)
def freeze_current_date():
    """Allow all calls to now or today to return the same date."""
    with freeze_time(TEST_CURRENT_DATE.strftime("%Y-%m-%d")):
        yield


@pytest.fixture(autouse=True)
def wide_scheduling_horizon(monkeypatch: pytest.MonkeyPatch):
    """Keep the date-mechanic tests focused on date/time handling rather than the scheduling limit:
    default to a horizon far beyond any date they pick. The dedicated horizon tests below override
    this within their own bodies."""
    monkeypatch.setattr(
        supporter.PolicyState,
        "config",
        LimitsConfig(free_scheduling_horizon_days=3650, patron_scheduling_horizon_days=3650),
    )


def expected_card(meeting: Meetup, lang: str, *, month: dt.date, **kwargs):
    """The datetime card the handler under test should have drawn, built the same way it does."""
    return screens.start_datetime_card(meeting, lang, month=month, **kwargs)


# ---------------------------------------------------------------------------
# Opening the card
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.OPEN_START_EDITOR.with_id(10)))],
    indirect=True,
)
async def test_open_start_editor_shows_the_card_on_the_meeting_month(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.OPEN_START_EDITOR, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert context.has_meeting_id(ContextId.EDIT_MEETING_START)
    context.api.assert_edit_message_called(
        update,
        expected_card(meeting, user_with_settings.lang, month=TEST_MEETING_DATETIME_UTC.date()),
    )


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.OPEN_START_EDITOR.with_id(10)))],
    indirect=True,
)
async def test_open_start_editor_without_datetime_opens_on_the_current_month(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.OPEN_START_EDITOR, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    today = user_with_settings.now_in_tz().date()
    context.api.assert_edit_message_called(update, expected_card(meeting, user_with_settings.lang, month=today))


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.REOPEN_START_EDITOR.with_id(10)))],
    indirect=True,
)
async def test_reopen_start_editor_shows_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.REOPEN_START_EDITOR, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    context.api.assert_edit_message_called(
        update,
        expected_card(meeting, user_with_settings.lang, month=TEST_MEETING_DATETIME_UTC.date()),
    )


# ---------------------------------------------------------------------------
# Month navigation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update,meeting,expected_month",
    [
        (
            UpdateRequest(callback_query=cb.NAVIGATE_START_CALENDAR.with_id(10).with_date(dt.date(2024, 12, 1))),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            dt.date(2024, 12, 1),
        ),
        (
            UpdateRequest(callback_query=cb.NAVIGATE_START_CALENDAR.with_id(10).with_date(TEST_CURRENT_DATE)),
            create_meetup(id=10, title="TestMeeting", description="Description"),
            TEST_CURRENT_DATE,
        ),
        (
            # A stale arrow pointing before today is clamped to the current month.
            UpdateRequest(callback_query=cb.NAVIGATE_START_CALENDAR.with_id(10).with_date(dt.date(2024, 1, 1))),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            TEST_CURRENT_DATE,
        ),
    ],
    indirect=["update"],
    ids=["forward_month", "dateless_meeting", "past_month_clamped_to_today"],
)
async def test_navigate_start_calendar_renders_the_requested_month(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    expected_month: dt.date,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.NAVIGATE_START_CALENDAR, handler_context=handler_context
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    context.api.assert_edit_message_called(
        update, expected_card(meeting, user_with_settings.lang, month=expected_month)
    )


# ---------------------------------------------------------------------------
# Picking a day
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update,current_datetime,expected_datetime",
    [
        (
            UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            None,
            # First date defaults to 23:59 in the owner timezone (Europe/Madrid, UTC+1 in December),
            # so 23:59 on 2024-12-21 local == 22:59 UTC the same day.
            dt.datetime(2024, 12, 21, 22, 59, tzinfo=dt.UTC),
        ),
        (
            UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            dt.datetime(2024, 11, 11, 12, 30, tzinfo=dt.UTC),
            # Changing only the date preserves the owner-local wall clock. Existing 12:30 UTC ==
            # 13:30 Madrid (Nov, UTC+1); on 2024-12-21 (Dec, UTC+1) 13:30 Madrid is again 12:30 UTC.
            dt.datetime.combine(TEST_MEETING_DATETIME_UTC.date(), dt.time(12, 30, tzinfo=dt.UTC)),
        ),
    ],
    indirect=["update"],
    ids=["set_date_for_the_first_time", "update_existing_date"],
)
async def test_pick_start_date_saves_and_redraws_the_card(
    mock_session: MockDbSession,
    update: Update,
    current_datetime: dt.datetime | None,
    expected_datetime: dt.datetime,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=current_datetime)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == expected_datetime

    # The card redraws in place showing the picked day highlighted; there is no success banner.
    # pick handlers render in the meeting's own language.
    context.api.assert_edit_message_called(
        update, expected_card(meeting, meeting.lang, month=TEST_MEETING_DATETIME_UTC.date())
    )
    context.api.assert_update_meeting_messages_called(meeting, None, True)
    metrics.assert_emitted(
        name=MetricKey.COUNT,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"EditedField": "datetime"},
    )


@pytest.mark.parametrize(
    "update",
    # Set date to 2026-01-15 — with existing time 12:30 UTC this becomes 2026-01-15 12:30 UTC
    # which is after END_DATETIME_FOR_ORDERING (2024-12-21 18:00 UTC), so end is cleared.
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2026, 1, 15)))],
    indirect=True,
)
async def test_set_date_past_end_datetime_clears_end_and_shows_alert(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Updating the date so start > end triggers enforce_datetime_ordering and shows an alert popup."""
    meeting = create_meetup(
        id=10,
        title="TestMeeting",
        description="Description",
        datetime=dt.datetime(2024, 11, 11, 12, 30, tzinfo=dt.UTC),
    )
    meeting.end_datetime = END_DATETIME_FOR_ORDERING
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2026, 6, 15)))],
    indirect=True,
)
async def test_set_date_first_time_clears_end_datetime_when_past_end(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Setting a date for the first time when end_datetime exists and new start > end triggers end_cleared."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    meeting.end_datetime = dt.datetime(2025, 1, 1, 12, 0, tzinfo=dt.UTC)
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # end_datetime was cleared because the new start (23:59 on 2026-06-15 Madrid == 21:59 UTC,
    # summer UTC+2) is after the old end (2025-01-01).
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.text(lang=meeting.lang),
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# The time row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update,meeting,expected_response",
    [
        (
            UpdateRequest(callback_query=cb.OPEN_START_TIME_PROMPT.with_id(10)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            ConversationMeetingState.START_DATETIME_CARD,
        ),
        (
            UpdateRequest(callback_query=cb.OPEN_START_TIME_PROMPT.with_id(11)),
            create_meetup(
                id=11,
                title="TestMeeting",
                description="Description",
                datetime=TEST_MEETING_DATETIME_UTC,
                owner=create_member(id=2, tg_user_id=456),
            ),
            # The guard rejection aborts the handler, so it ends the conversation.
            ConversationHandler.END,
        ),
    ],
    indirect=["update"],
    ids=["edit_meeting_time", "edit_meeting_time_not_accessible"],
)
async def test_time_edit_chip_asks_for_a_typed_time(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    expected_response: ConversationMeetingState | int,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    if meeting.db_id == 10:
        user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.OPEN_START_TIME_PROMPT, handler_context=handler_context)

    assert response == expected_response
    # The chip stores the meeting id for the message step that follows it; a rejection ends the
    # conversation instead and stores nothing.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_START) == (expected_response != ConversationHandler.END)

    if expected_response == ConversationMeetingState.START_DATETIME_CARD:
        # The prompt replaces the card in place, so no interactive card lingers above the fresh
        # one the typed answer earns. It renders in the meeting's own language.
        context.api.assert_edit_message_called(
            update, screens.time_prompt_view(meeting.lang, cb.REOPEN_START_EDITOR.with_id(10))
        )

    if expected_response == ConversationHandler.END:
        metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    else:
        metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=0)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.OPEN_START_TIME_PROMPT.with_id(10))],
    indirect=True,
)
async def test_time_edit_chip_on_a_dateless_meeting_still_prompts(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A stale Edit chip can be tapped after the date was removed: the prompt still replaces the
    message, and the typed time then answers with the select-a-date-first error."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.OPEN_START_TIME_PROMPT, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    context.api.assert_edit_message_called(
        update, screens.time_prompt_view(meeting.lang, cb.REOPEN_START_EDITOR.with_id(10))
    )


# ---------------------------------------------------------------------------
# Typed time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(message_text="20:20")], indirect=["update"])
@freeze_time("2024-12-31 23:20:00", tz_offset=0)  # Freeze UTC time just before midnight to test timezone conversion
async def test_set_time_message_with_valid_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    # Future meeting date (after the frozen now) so the new start passes the past-datetime check.
    meeting = create_meetup(
        id=10, title="TestMeeting", description="Description", datetime=dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC)
    )
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # Since the user provides 20:20 in Europe/Madrid, the UTC time stored in the meeting is one hour earlier
    assert meeting.datetime == dt.datetime(2025, 1, 15, 19, 20, tzinfo=dt.UTC)

    # The conversation stays on the card, so the meeting id survives for the next typed input.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_START)

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=0)
    metrics.assert_emitted(
        name=MetricKey.COUNT,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"EditedField": "datetime"},
    )
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)

    context.api.assert_send_message_called(
        update, expected_card(meeting, user_with_settings.lang, month=dt.date(2025, 1, 15))
    )
    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="20:20")], indirect=["update"])
async def test_set_time_without_a_date_is_refused(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A time is a property of a scheduled day: with no date set, HH:MM answers with the
    select-a-date-first error instead of inventing a day."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is None
    mock_session.assert_not_flushed()

    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=today,
            error=MeetingEditDateTimeMessages.TIME_NEEDS_DATE.rich(lang=user_with_settings.lang),
        ),
    )
    metrics.assert_emitted(
        name=MetricKey.ERROR,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"reason": "time_without_date"},
        value=1,
    )


@pytest.mark.parametrize("update", [(UpdateRequest(message_text="49:20"))], indirect=["update"])
async def test_set_time_message_with_invalid_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    assert context.has_meeting_id(ContextId.EDIT_MEETING_START)

    # The card comes back with the invalid-value notice on top, so every control stays reachable.
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=TEST_MEETING_DATETIME_UTC.date(),
            error=CommonMessages.TIME_INVALID_VALUE.rich(lang=user_with_settings.lang),
        ),
    )

    metrics.assert_emitted(
        name=MetricKey.ERROR,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"reason": "invalid_time"},
        value=1,
    )
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


# ---------------------------------------------------------------------------
# Typed datetime entity
# ---------------------------------------------------------------------------


DATE_TIME_ENTITY_UNIX_TIME = 1735000000
DATE_TIME_ENTITY_TEXT = "Tomorrow at noon"
DATE_TIME_ENTITY_REQUEST = UpdateRequest(
    message_text=DATE_TIME_ENTITY_TEXT,
    entities=[
        MessageEntity(
            type=MessageEntity.DATE_TIME,
            offset=0,
            length=len(DATE_TIME_ENTITY_TEXT),
            unix_time=dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME, tz=dt.UTC),
        )
    ],
)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST], indirect=True)
async def test_type_start_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    expected_datetime = dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME, tz=dt.UTC)
    assert meeting.datetime == expected_datetime

    month = meeting.owner.datetime_in_tz(expected_datetime).date()
    context.api.assert_send_message_called(update, expected_card(meeting, user_with_settings.lang, month=month))
    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST], indirect=True)
async def test_type_start_datetime_user_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """TYPE_START_DATETIME stops on UserNotFound when the user is not registered.

    The error handler answers a missing account as an expected business state, so the invocation
    still closes with one `Fault` sample and it carries a 0.
    """
    # Do not add the user to the session: the guard raises UserNotFound.

    context, _ = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 99},
    )

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST], indirect=True)
async def test_type_start_datetime_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """TYPE_START_DATETIME stops when the meeting is not accessible to the user."""
    not_owned_meeting = create_meetup(id=99, title="Not Owned", owner=create_member(id=2, tg_user_id=456))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(not_owned_meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 99},
    )

    assert response == ConversationHandler.END

    # A message update carries no message of ours to replace, so the redirect is a fresh reply.
    context.api.assert_send_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    context.api.assert_edit_message_not_called()


# ---------------------------------------------------------------------------
# Input the screen cannot use
# ---------------------------------------------------------------------------


def entry_point_update(update: Update):
    return Update(
        123,
        callback_query=CallbackQuery(
            id="123",
            from_user=cast(TgUser, update.effective_user),
            message=update.effective_message,
            data=str(cb.OPEN_START_EDITOR.with_id(10)),
            chat_instance="instance",
        ),
    )


@pytest.mark.parametrize(
    "update",
    [
        (UpdateRequest(message_text="Some text")),
        (UpdateRequest(location=Location(latitude=0, longitude=0))),
    ],
    ids=["update_with_text", "update_with_location"],
    indirect=["update"],
)
async def test_conversation_fallback_with_wrong_message_format(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Lets first trigger the conversation (use a separate client to not pollute the metrics we want to assert)
    ctx = HandlerContext(update=entry_point_update(update), app=app, metrics_client=make_test_metrics_client())
    context, _ = await call_handler(
        EditMeetingHandlerId.START_EDITOR_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    # Now answer with a message the card cannot use
    context, _ = await call_handler(EditMeetingHandlerId.START_EDITOR_CONVERSATION, handler_context=handler_context)

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_START)

    # The card comes back with the invalid-format notice on top, so every control stays reachable
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=TEST_MEETING_DATETIME_UTC.date(),
            error=CommonMessages.DATETIME_INPUT_INVALID.rich(
                lang=user_with_settings.lang, datetime_link=datetime_link_content()
            ),
        ),
        times=1,
    )

    metrics.assert_emitted(
        name=MetricKey.ERROR,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"reason": "wrong_datetime_format"},
        value=1,
    )
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


# ---------------------------------------------------------------------------
# Leaving the flow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.CANCEL_START_EDIT.with_id(10)))],
    indirect=True,
)
async def test_start_card_can_be_cancelled(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    ctx = HandlerContext(update=entry_point_update(update), app=app, metrics_client=make_test_metrics_client())
    context, _ = await call_handler(
        EditMeetingHandlerId.START_EDITOR_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )
    context, _ = await call_handler(EditMeetingHandlerId.START_EDITOR_CONVERSATION, handler_context=handler_context)

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_START)

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting), times=1)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.CANCEL_START_EDIT.with_id(10)))],
    indirect=["update"],
    ids=["cancel_start_edit"],
)
async def test_cancel_start_edit(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """CANCEL_START_EDIT cleans up context and returns ConversationHandler.END, showing the editor."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.CANCEL_START_EDIT,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_START)
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))


# --- enforce_datetime_ordering: setting start past end clears end_datetime ---


END_DATETIME_FOR_ORDERING = dt.datetime(2024, 12, 21, 18, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="20:20")], indirect=True)
@freeze_time("2024-12-31 23:20:00", tz_offset=0)
async def test_set_time_past_end_datetime_clears_end(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """When new start time is after end time, enforce_datetime_ordering clears end_datetime."""
    meeting = create_meetup(
        id=10, title="TestMeeting", description="Description", datetime=dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC)
    )
    meeting.end_datetime = END_DATETIME_FOR_ORDERING
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD

    # The user sets 20:20 in Europe/Madrid (UTC+1) on the meeting's date, stored as 2025-01-15 19:20 UTC.
    # 2025-01-15 19:20 UTC > 2024-12-21 18:00 UTC end, so end_datetime is cleared.
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    # The end getting cleared is the one side effect the card does not explain, so it rides on top.
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=dt.date(2025, 1, 15),
            error=MeetingEditDateTimeMessages.END_CLEARED_BY_START.rich(lang=user_with_settings.lang),
        ),
    )


DATE_TIME_ENTITY_UNIX_TIME_FUTURE = 1770000000  # 2026-02-02 ~07:00 UTC — after END_DATETIME_FOR_ORDERING
DATE_TIME_ENTITY_TEXT_FUTURE = "Next month at noon"
DATE_TIME_ENTITY_REQUEST_FUTURE = UpdateRequest(
    message_text=DATE_TIME_ENTITY_TEXT_FUTURE,
    entities=[
        MessageEntity(
            type=MessageEntity.DATE_TIME,
            offset=0,
            length=len(DATE_TIME_ENTITY_TEXT_FUTURE),
            unix_time=dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME_FUTURE, tz=dt.UTC),
        )
    ],
)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST_FUTURE], indirect=True)
async def test_datetime_entity_past_end_datetime_clears_end(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """When a datetime entity sets a start past end_datetime, enforce_datetime_ordering clears end_datetime."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    meeting.end_datetime = END_DATETIME_FOR_ORDERING
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    expected_datetime = dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME_FUTURE, tz=dt.UTC)
    assert meeting.datetime == expected_datetime
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    month = meeting.owner.datetime_in_tz(expected_datetime).date()
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=month,
            error=MeetingEditDateTimeMessages.END_CLEARED_BY_START.rich(lang=user_with_settings.lang),
        ),
    )
    context.api.assert_update_meeting_messages_called(meeting)


# ---------------------------------------------------------------------------
# validate_start_datetime — now-relative past validation (START_IN_PAST)
#
# "now" is meeting.owner.now_in_tz().astimezone(UTC). The owner uses Europe/Madrid
# (user_with_settings), so tests freeze a fixed UTC instant and use past/future dates
# relative to it. Comparison is <=, so exactly "now" is rejected.
# ---------------------------------------------------------------------------


# Frozen in winter so Europe/Madrid is a clean UTC+1 (no DST ambiguity).
START_PAST_FROZEN_NOW = "2025-01-15 12:00:00"  # UTC
# A date well before the frozen now, used for past-start scenarios.
PAST_DATE = dt.date(2025, 1, 10)


def datetime_entity_request(unix_time: int) -> UpdateRequest:
    """Build an UpdateRequest carrying a single date_time entity at *unix_time*."""
    text = "Some moment"
    return UpdateRequest(
        message_text=text,
        entities=[
            MessageEntity(
                type=MessageEntity.DATE_TIME,
                offset=0,
                length=len(text),
                unix_time=dt.datetime.fromtimestamp(unix_time, tz=dt.UTC),
            )
        ],
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_first_time_in_past_shows_alert_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A past first date shows the START_IN_PAST alert and saves nothing."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_update_to_past_shows_alert_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Moving an existing datetime onto a past date shows the alert and saves nothing."""
    # Existing future datetime; the click keeps its 10:00 time but moves it to the past PAST_DATE.
    existing = dt.datetime(2025, 1, 20, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == existing  # unchanged
    mock_session.assert_not_flushed()
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_update_to_past_with_naive_stored_datetime_does_not_raise(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """The pick tolerates a naive stored meeting.datetime when validating the past start."""
    naive_existing = dt.datetime(2025, 1, 20, 10, 0)  # naive
    assert naive_existing.tzinfo is None
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=naive_existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == naive_existing  # unchanged, no TypeError raised
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    # date_time entity at 2025-01-10 10:00 UTC — before the frozen now (2025-01-15).
    [datetime_entity_request(int(dt.datetime(2025, 1, 10, 10, 0, tzinfo=dt.UTC).timestamp()))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_start_datetime_entity_in_past_sends_error_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A past entity answers with START_IN_PAST on the card and saves nothing."""
    existing = dt.datetime(2025, 1, 20, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == existing  # unchanged
    mock_session.assert_not_flushed()
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=existing.date(),
            error=MeetingEditDateTimeMessages.START_IN_PAST.rich(lang=user_with_settings.lang),
        ),
    )


@pytest.mark.parametrize(
    "update",
    # Entity exactly at the frozen now — rejected because the comparison is <=.
    [datetime_entity_request(int(dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC).timestamp()))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_start_datetime_entity_exactly_now_is_rejected(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A start datetime exactly equal to now is rejected (comparison is <=, not <)."""
    existing = dt.datetime(2025, 1, 20, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == existing  # unchanged
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=existing.date(),
            error=MeetingEditDateTimeMessages.START_IN_PAST.rich(lang=user_with_settings.lang),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="10:00")],  # 10:00 Madrid on a past meeting date → past start
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_time_in_past_sends_error_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A time on a past meeting date answers with START_IN_PAST on the card."""
    # Meeting date is in the past (PAST_DATE); the handler keeps that date and applies 10:00 Madrid,
    # producing a past start (2025-01-10 09:00 UTC) relative to the frozen now.
    past_meeting = dt.datetime(2025, 1, 10, 8, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=past_meeting)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime == past_meeting  # unchanged
    mock_session.assert_not_flushed()
    # A past date renders on the current month: the card never opens on a month with no
    # schedulable day.
    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        expected_card(
            meeting,
            user_with_settings.lang,
            month=today,
            error=MeetingEditDateTimeMessages.START_IN_PAST.rich(lang=user_with_settings.lang),
        ),
    )


# ---------------------------------------------------------------------------
# Scheduling horizon — enforced only on date picks, never on time-only edits
#
# Frozen now is 2025-01-15 (UTC). The owner is Europe/Madrid. The free horizon is 31 days, so
# 2025-02-15 is the boundary (allowed) and 2025-02-16 is one day past it (rejected).
# ---------------------------------------------------------------------------

HORIZON_FROZEN_NOW = "2025-01-15 12:00:00"  # UTC
HORIZON_BOUNDARY_DATE = dt.date(2025, 2, 15)  # exactly 31 days ahead
HORIZON_BEYOND_DATE = dt.date(2025, 2, 16)  # 32 days ahead


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(HORIZON_BEYOND_DATE))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_date_beyond_horizon_shows_upsell_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A free user picking a date past the horizon gets the upsell; the date is not saved."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    # The rejection replaces the card in place: no alert, the Collaborate button, and a button
    # back to the card. It renders in the owner's language (what scheduling_horizon_rejection
    # uses), not the meeting's content language.
    context.api.assert_method_just_called("answer_callback_query", times=0)
    calendar_button = ButtonConfig(
        text=ButtonMessages.DATE_TIME.back(lang=user_with_settings.lang),
        callback_data=cb.NAVIGATE_START_CALENDAR.with_id(10).with_date(dt.date(2025, 1, 15)),
    )
    context.api.assert_edit_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON.rich(lang=user_with_settings.lang, days=31),
            user_with_settings.lang,
        ).with_context_menu([[calendar_button]]),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(HORIZON_BOUNDARY_DATE))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_date_exactly_on_horizon_is_allowed(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """The boundary date (exactly the horizon) is accepted and saved."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is not None  # saved
    assert meeting.owner.datetime_in_tz(meeting.datetime).date() == HORIZON_BOUNDARY_DATE
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(HORIZON_BEYOND_DATE))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_date_beyond_free_horizon_allowed_for_premium(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A Patron owner gets the extended horizon, so a date past the free limit is accepted."""
    monkeypatch.setattr(
        supporter.PolicyState,
        "config",
        LimitsConfig(free_scheduling_horizon_days=31, patron_scheduling_horizon_days=365),
    )
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is not None  # saved
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="20:20")], indirect=["update"])
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_time_edit_on_grandfathered_far_future_meeting_is_not_blocked(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """Grandfathering: a meeting already scheduled far beyond the horizon can still have its time
    edited. The horizon check applies to new date picks only, not to time-only edits."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    # Existing start is ~5 months out, well beyond the 31-day horizon.
    far_future = dt.datetime(2025, 6, 1, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=far_future)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_TIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # Time updated to 20:20 Madrid (19:20 UTC) while the far-future date is preserved.
    assert meeting.datetime == dt.datetime(2025, 6, 1, 18, 20, tzinfo=dt.UTC)
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "update",
    # date_time entity 60 days ahead of the frozen now — beyond the 31-day free horizon.
    [datetime_entity_request(int(dt.datetime(2025, 3, 16, 12, 0, tzinfo=dt.UTC).timestamp()))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_start_datetime_entity_beyond_horizon_sends_upsell_and_stays_on_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A beyond-horizon entity sends the horizon rejection with the Collaborate button as a reply."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.TYPE_START_DATETIME,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_START: 10},
    )

    assert response == ConversationMeetingState.START_DATETIME_CARD
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    entry_back_button = ButtonConfig(
        text=ButtonMessages.DATE_TIME.back(lang=user_with_settings.lang),
        callback_data=cb.REOPEN_START_EDITOR.with_id(10),
    )
    context.api.assert_send_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON.rich(lang=user_with_settings.lang, days=31),
            user_with_settings.lang,
        ).with_context_menu([[entry_back_button]]),
    )


# ---------------------------------------------------------------------------
# START date-first default is 23:59 in the owner timezone.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    # Pick TODAY (frozen to 2024-11-15) as the very first date.
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2024, 11, 15)))],
    indirect=True,
)
@freeze_time("2024-11-15 08:00:00", tz_offset=0)  # 08:00 UTC == 09:00 Madrid, earlier in the day
async def test_set_date_first_time_today_succeeds_with_2359_default(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Picking TODAY as the first date succeeds: the default of 23:59 (owner-local) is still in
    the future."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # 23:59 on 2024-11-15 Madrid (Nov, UTC+1) == 22:59 UTC the same day.
    assert meeting.datetime == dt.datetime(2024, 11, 15, 22, 59, tzinfo=dt.UTC)
    context.api.assert_method_just_called("answer_callback_query", times=0)


# ---------------------------------------------------------------------------
# The pick preserves the owner-LOCAL wall clock across DST and naive storage.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    # Move the date across the spring DST boundary into summer time.
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2026, 4, 10)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_set_date_update_preserves_local_time_across_dst(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Changing only the date across a DST boundary keeps the owner-LOCAL wall clock time. The
    stored UTC value shifts by the offset delta; the displayed local time does not move."""
    # Existing start: 2026-03-20 09:00 UTC == 10:00 Madrid (winter, UTC+1).
    existing = dt.datetime(2026, 3, 20, 9, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    # Local wall clock before the change is 10:00.
    assert meeting.owner.datetime_in_tz(existing).time() == dt.time(10, 0)

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # 2026-04-10 is summer time (Madrid UTC+2), so 10:00 local == 08:00 UTC — the UTC time-of-day
    # shifted by one hour relative to the winter value (09:00 UTC).
    assert meeting.datetime == dt.datetime(2026, 4, 10, 8, 0, tzinfo=dt.UTC)
    # The owner-local wall clock is preserved at 10:00.
    assert meeting.datetime is not None
    assert meeting.owner.datetime_in_tz(meeting.datetime).time() == dt.time(10, 0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2026, 6, 25)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_set_date_update_with_naive_stored_datetime_treats_it_as_utc(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A naive stored meeting.datetime is read as UTC before the local-time preservation, so it is
    never reinterpreted against the system timezone."""
    # A naive value reads as 2026-06-20 10:00 UTC.
    naive_existing = dt.datetime(2026, 6, 20, 10, 0)
    assert naive_existing.tzinfo is None
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=naive_existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # 10:00 UTC == 12:00 Madrid (summer, UTC+2); preserved on 2026-06-25 that local 12:00 == 10:00 UTC.
    assert meeting.datetime == dt.datetime(2026, 6, 25, 10, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.PICK_START_DATE.with_id(10).with_date(dt.date(2026, 4, 10)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_set_date_update_utc_owner_is_local_time_noop(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Sanity: for a UTC owner (no DST) changing only the date is a no-op on the local time-of-day."""
    user_with_settings.settings.timezone = "UTC"
    # Existing start: 2026-03-20 09:00 UTC (== 09:00 local for a UTC owner).
    existing = dt.datetime(2026, 3, 20, 9, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.PICK_START_DATE, handler_context=handler_context)

    assert response == ConversationMeetingState.START_DATETIME_CARD
    # UTC owner: the time-of-day is unchanged, only the date moves.
    assert meeting.datetime == dt.datetime(2026, 4, 10, 9, 0, tzinfo=dt.UTC)
