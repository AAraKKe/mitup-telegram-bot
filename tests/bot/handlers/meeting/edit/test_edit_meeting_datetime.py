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
from mitup_bot.handlers.meeting.edit.edit_meeting_datetime import build_edit_datetime_entry_view as build_entry_view
from mitup_bot.handlers.meeting.edit.edit_meeting_datetime import build_edit_time_prompt_view
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MeetupMessage
from mitup_bot.monitoring import Feature, MetricKey, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils import render
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingDisplayMessages,
    MeetingEditDateTimeMessages,
    SupporterMessages,
)
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    create_meetup,
    make_test_metrics_client,
)
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

TEST_MEETING_DATETIME_UTC = dt.datetime(2024, 12, 21, 12, 0, tzinfo=dt.UTC)
TEST_CURRENT_DATE = dt.date(2024, 11, 15)
TEST_31ST_DATETIME = dt.datetime(2024, 10, 31, 12, 30, 0, tzinfo=dt.UTC)


def set_new_date_view(lang: str, meeting_id: int, datetime: FormattedText) -> MitupView:
    # Production code shows a single Done button (cb.CANCEL_EDIT_START_TIME) after the first date is set.
    # The message has only a ${datetime} placeholder — no ${done_button}.
    return MitupView(
        description=MeetingEditDateTimeMessages.DATE_ADDED_TIME_PROMPT.get(
            lang=lang,
            datetime=datetime,
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get_text(lang=lang),
                    callback_data=cb.CANCEL_EDIT_START_TIME.with_id(meeting_id),
                ),
            ]
        ],
    )


def update_meeting_view(meeting: Meetup, datetime: FormattedText) -> MitupView:
    return meeting_views.edit_view(meeting).with_context(
        MeetingEditDateTimeMessages.DATE_UPDATED.get(lang=meeting.lang, datetime=datetime)
    )


def datetime_entity(unix_time: int) -> FormattedText:
    """Build the FormattedText that the handler produces for a set datetime."""
    unix_dt = dt.datetime.fromtimestamp(unix_time, tz=dt.UTC)
    return render(t"{EntityDateTime('Meeting time', unix_time=unix_dt, date_time_format='DT')}")


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


@pytest.mark.parametrize(
    "update,meeting,anchor_date,current_date",
    [
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            TEST_MEETING_DATETIME_UTC.date(),
            TEST_MEETING_DATETIME_UTC.date(),
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_id(10).with_date(TEST_CURRENT_DATE)),
            create_meetup(id=10, title="TestMeeting", description="Description"),
            TEST_CURRENT_DATE,
            TEST_CURRENT_DATE,
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_id(10).with_date(TEST_CURRENT_DATE)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            TEST_MEETING_DATETIME_UTC,
            TEST_CURRENT_DATE,
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_id(10).with_date(TEST_CURRENT_DATE)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_31ST_DATETIME),
            TEST_CURRENT_DATE,
            TEST_CURRENT_DATE,
        ),
    ],
    indirect=["update"],
    ids=["meeting_dt_set", "meeting_dt_not_set", "meeting_dt_set_different_current_date", "meeting_dt_on_31st"],
)
async def test_edit_meeting_date_callback(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    anchor_date: dt.date,
    current_date: dt.date,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATE
    context.api.assert_edit_message_called(
        update,
        factory.edit_meeting_date_view(
            RenderContext(lang=user_with_settings.lang),
            meeting_id=10,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.datetime is None,
        ),
    )


@pytest.mark.parametrize(
    "update,current_datetime,new",
    [
        (
            UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            None,
            True,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            dt.datetime(2024, 11, 11, 12, 30, tzinfo=dt.UTC),
            False,
        ),
    ],
    indirect=["update"],
    ids=["set_date_for_the_first_time", "update_existing_date"],
)
async def test_set_meeting_date_callback(
    mock_session: MockDbSession,
    update: Update,
    current_datetime: dt.datetime,
    new: bool,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=current_datetime)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Lets add a message to validate it has been updated
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    expected_datetime = (
        # First date defaults to 23:59 in the owner timezone (Europe/Madrid, UTC+1 in December),
        # so 23:59 on 2024-12-21 local == 22:59 UTC the same day.
        dt.datetime(2024, 12, 21, 22, 59, tzinfo=dt.UTC)
        if new
        # Changing only the date preserves the owner-local wall clock. Existing 12:30 UTC ==
        # 13:30 Madrid (Nov, UTC+1); on 2024-12-21 (Dec, UTC+1) 13:30 Madrid is again 12:30 UTC —
        # no DST crossed, so the UTC value is unchanged.
        else dt.datetime.combine(TEST_MEETING_DATETIME_UTC.date(), dt.time(12, 30, tzinfo=dt.UTC))
    )

    assert meeting.datetime == expected_datetime

    if new:
        # First time setting the date: show a "done" prompt and advance to EDIT_TIME.
        assert response == ConversationMeetingState.EDIT_TIME
        # handle_first_datetime_set uses meeting.lang (not user.lang), so the view is in the meeting's language.
        expected_view = set_new_date_view(
            meeting.lang,
            10,
            datetime_entity(int(expected_datetime.timestamp())),
        )
        context.api.assert_edit_message_called(update, expected_view)
        context.api.assert_update_meeting_messages_called(meeting, None, True)
    else:
        # Updating an existing datetime: re-show entry view with success context, return EDIT_DATETIME.
        # The handler uses meeting.lang (the meeting's own language, "en" from create_meetup), not the
        # user's session language — so both en and es_ES user variants produce the same English view.
        # today must be obtained under freeze_time to match the FakeDate from meeting.owner.now_in_tz().
        assert response == ConversationMeetingState.EDIT_DATETIME
        today = meeting.owner.now_in_tz().date()
        dt_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), expected_datetime, "DT")
        expected_view = build_entry_view(meeting, meeting.lang, today).with_context(
            MeetingEditDateTimeMessages.DATE_UPDATED.get(
                lang=meeting.lang,
                datetime=render(t"{dt_entity}"),
            )
        )
        context.api.assert_edit_message_called(update, expected_view)
        context.api.assert_update_meeting_messages_called(meeting, None, True)


# ---------------------------------------------------------------------------
# SET_DATE_CALLBACK — date selection that clears end_datetime shows alert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    # Set date to 2026-01-15 — with existing time 12:30 UTC this becomes 2026-01-15 12:30 UTC
    # which is after END_DATETIME_FOR_ORDERING (2024-12-21 18:00 UTC), so end is cleared.
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2026, 1, 15)))],
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

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update,meeting,expected_response",
    [
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(10)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            ConversationMeetingState.EDIT_TIME,
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(11)),
            create_meetup(id=11, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            ConversationHandler.END,
        ),
    ],
    indirect=["update"],
    ids=["edit_meeting_time", "edit_meeting_time_not_accessible"],
)
async def test_edit_meeting_time_callback(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    expected_response: int,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    if meeting.db_id == 10:
        user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.EDIT_TIME_CALLBACK, handler_context=handler_context)

    assert response == expected_response
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME) or expected_response == ConversationHandler.END

    # show_edit_time_prompt uses meeting.lang (the meeting's own language), not user.lang
    expected_view = MitupView(
        description=CommonMessages.TIME_PROMPT.get(lang=meeting.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=meeting.lang),
                    callback_data=cb.CANCEL_EDIT_START_TIME.with_id(10),
                )
            ]
        ],
    )

    if expected_response == ConversationMeetingState.EDIT_TIME:
        context.api.assert_edit_message_called(update, expected_view)

    if expected_response == ConversationHandler.END:
        metrics.assert_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=1)
    else:
        metrics.assert_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=0)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update, meeting, expected_meeting_time",
    [
        (
            UpdateRequest(message_text="20:20"),
            # Future meeting date (after the frozen now) so the new start passes the past-datetime check.
            create_meetup(
                id=10,
                title="TestMeeting",
                description="Description",
                datetime=dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC),
            ),
            dt.datetime(2025, 1, 15, 19, 20, tzinfo=dt.UTC),
        ),
        (
            UpdateRequest(message_text="20:20"),
            create_meetup(id=10, title="TestMeeting", description="Description"),
            dt.datetime(2025, 1, 1, 19, 20, tzinfo=dt.UTC),
        ),
    ],
    indirect=["update"],
    ids=["meeting_with_time", "meeting_without_time"],
)
@freeze_time("2024-12-31 23:20:00", tz_offset=0)  # Freeze UTC time just before midnight to test timezone conversion
async def test_set_time_message_with_valid_time(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    expected_meeting_time: dt.datetime,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    # Since the user provides 20:20 in Europ/Madrid, the UTC time stored in the meeting is one hour earlier
    assert meeting.datetime == expected_meeting_time

    # Meeting id has been removed from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=0)
    metrics.assert_emitted(
        name=MetricKey.COUNT,
        dimensions={"Feature": str(Feature.EDIT_MEETING)},
        properties={"EditedField": "datetime"},
    )
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)

    context.api.assert_send_message_called(
        update,
        meeting_views.when_view(meeting).with_context(
            MeetingEditDateTimeMessages.TIME_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(int(expected_meeting_time.timestamp())),
            )
        ),
    )
    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(message_text="49:20"))],
    indirect=["update"],
    ids=["update"],
)
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
        EditMeetingHandlerId.SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_TIME
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Time prompt resent with the invalid-value notice on top, so the Cancel button stays reachable
    context.api.assert_send_message_called(
        update,
        build_edit_time_prompt_view(
            meeting, user_with_settings.lang, error=CommonMessages.TIME_INVALID_VALUE.get(lang=user_with_settings.lang)
        ),
    )

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("InvalidTime"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


def entry_point_update(update: Update):
    return Update(
        123,
        callback_query=CallbackQuery(
            id="123",
            from_user=cast(TgUser, update.effective_user),
            message=update.effective_message,
            data=str(cb.SET_MEETING_START_TIME.with_id(10)),
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
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    # Now answer with a wrong message format (in EDIT_DATETIME state, the fallback is WRONG_DATETIME_MESSAGE)
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, handler_context=handler_context)

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Entry prompt resent with the invalid-format notice on top, so the Date/Time buttons stay reachable
    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        build_entry_view(
            meeting,
            user_with_settings.lang,
            today,
            error=CommonMessages.DATETIME_INVALID.get(
                lang=user_with_settings.lang, datetime_link=build_datetime_link()
            ),
        ),
        times=1,
    )

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME.with_id(10)))],
    indirect=True,
)
async def test_edit_time_can_be_cancelled(
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
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, handler_context=handler_context)

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    context.api.assert_edit_message_called(update, meeting_views.when_view(meeting), times=1)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.SET_MEETING_START_TIME.with_id(10)))],
    indirect=True,
)
async def test_date_time_entry_callback(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK, handler_context=handler_context
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Use user.now_in_tz().date() under freeze_time to get the FakeDate the handler uses for the [Date] button
    today = user_with_settings.now_in_tz().date()
    datetime_link = build_datetime_link()
    expected_view = MitupView(
        description=MeetingEditDateTimeMessages.DESCRIPTION.get(
            lang=user_with_settings.lang, datetime_link=datetime_link
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_DATE.with_id(10).with_date(today),
                ),
                ButtonConfig(
                    text=ButtonMessages.TIME.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_TIME.with_id(10),
                ),
            ],
        ],
    ).with_back_button(ButtonMessages.WHEN, user_with_settings.lang, cb.CANCEL_EDIT_START_TIME.with_id(10))
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.SET_MEETING_START_TIME.with_id(10)))],
    indirect=["update"],
    ids=["date_time_entry_no_datetime"],
)
async def test_date_time_entry_callback_without_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """DATE_TIME_ENTRY_CALLBACK with meeting.datetime=None uses the same keyboard (no conditional rows)."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK, handler_context=handler_context
    )

    assert response == ConversationMeetingState.EDIT_DATETIME

    today = user_with_settings.now_in_tz().date()
    datetime_link = build_datetime_link()
    expected_view = MitupView(
        description=MeetingEditDateTimeMessages.DESCRIPTION.get(
            lang=user_with_settings.lang, datetime_link=datetime_link
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_DATE.with_id(10).with_date(today),
                ),
                ButtonConfig(
                    text=ButtonMessages.TIME.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_TIME.with_id(10),
                ),
            ],
        ],
    ).with_back_button(ButtonMessages.WHEN, user_with_settings.lang, cb.CANCEL_EDIT_START_TIME.with_id(10))
    context.api.assert_edit_message_called(update, expected_view)


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
async def test_date_time_entity_message(
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
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    expected_datetime = dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME, tz=dt.UTC)
    assert meeting.datetime == expected_datetime

    context.api.assert_send_message_called(
        update,
        meeting_views.when_view(meeting).with_context(
            MeetingEditDateTimeMessages.DATE_UPDATED.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(DATE_TIME_ENTITY_UNIX_TIME),
            )
        ),
    )
    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize(
    "update,handler_id,expected_state",
    [
        (
            UpdateRequest(message_text="some plain text"),
            EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
            ConversationMeetingState.EDIT_DATETIME,
        ),
        (
            UpdateRequest(location=Location(latitude=0, longitude=0)),
            EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
            ConversationMeetingState.EDIT_DATETIME,
        ),
    ],
    indirect=["update"],
    ids=["plain_text_in_edit_datetime_state", "non_text_in_edit_datetime_state"],
)
async def test_datetime_state_fallbacks(
    mock_session: MockDbSession,
    update: Update,
    handler_id: EditMeetingHandlerId,
    expected_state: ConversationMeetingState,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Plain text and non-text messages in EDIT_DATETIME state resend the entry prompt (with the
    invalid-format notice and Date/Time buttons) and stay in EDIT_DATETIME, so the user is not stranded."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        handler_id, handler_context=handler_context, with_meeting_id={ContextId.EDIT_MEETING_TIME: 10}
    )

    assert response == expected_state
    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        build_entry_view(
            meeting,
            user_with_settings.lang,
            today,
            error=CommonMessages.DATETIME_INVALID.get(
                lang=user_with_settings.lang, datetime_link=build_datetime_link()
            ),
        ),
    )
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST], indirect=True)
async def test_date_time_entity_message_user_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """DATE_TIME_ENTITY_MESSAGE raises UserNotFound when the user is not registered."""
    # Do not add the user to the session — guard raises UserNotFound.

    context, _ = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    )

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("UserNotFound"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize("update", [DATE_TIME_ENTITY_REQUEST], indirect=True)
async def test_date_time_entity_message_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """DATE_TIME_ENTITY_MESSAGE stops when the meeting is not accessible to the user."""
    not_owned_meeting = create_meetup(id=99, title="Not Owned")
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(not_owned_meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    )

    assert response == ConversationHandler.END

    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME.with_id(10)))],
    indirect=["update"],
    ids=["cancel_start_time"],
)
async def test_cancel_start_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """CANCEL_START_TIME_CALLBACK cleans up context and returns ConversationHandler.END, showing when_view."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    # cleanup_states must have removed EDIT_MEETING_TIME from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)
    context.api.assert_edit_message_called(update, meeting_views.when_view(meeting))


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(10)))],
    indirect=["update"],
    ids=["back_to_edit_datetime"],
)
async def test_back_to_edit_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """BACK_TO_EDIT_DATETIME_CALLBACK re-shows the EDIT_DATETIME entry view and returns EDIT_DATETIME."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        handler_context=handler_context,
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    # Use meeting.owner.now_in_tz().date() to get the same FakeDate that the handler produces under freeze_time
    today = meeting.owner.now_in_tz().date()
    context.api.assert_edit_message_called(update, build_entry_view(meeting, user_with_settings.lang, today))


# --- enforce_datetime_ordering: setting start past end clears end_datetime ---


END_DATETIME_FOR_ORDERING = dt.datetime(2024, 12, 21, 18, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="20:20")],
    indirect=True,
)
@freeze_time("2024-12-31 23:20:00", tz_offset=0)
async def test_set_time_past_end_datetime_clears_end(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """When new start time is after end time, enforce_datetime_ordering clears end_datetime."""
    # Future meeting date (after the frozen now) so the new start passes the past-datetime check;
    # the handler keeps this date and applies the typed 20:20.
    meeting = create_meetup(
        id=10,
        title="TestMeeting",
        description="Description",
        datetime=dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC),
    )
    meeting.end_datetime = END_DATETIME_FOR_ORDERING
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END

    # The user sets 20:20 in Europe/Madrid (UTC+1) on the meeting's date, stored as 2025-01-15 19:20 UTC.
    # 2025-01-15 19:20 UTC > 2024-12-21 18:00 UTC end, so end_datetime is cleared.
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    # The context message should include END_DATETIME_CLEARED_BY_START prepended
    assert meeting.datetime is not None
    expected_time_entity = datetime_entity(int(meeting.datetime.timestamp()))
    expected_context_message = (
        MeetingEditDateTimeMessages.END_CLEARED_BY_START.get(lang=user_with_settings.lang)
        .append("\n\n")
        .append(
            MeetingEditDateTimeMessages.TIME_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=expected_time_entity,
            )
        )
    )
    context.api.assert_send_message_called(
        update,
        meeting_views.when_view(meeting).with_context(expected_context_message),
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


# ---------------------------------------------------------------------------
# handle_first_datetime_set — end_cleared path (lines 258-263, 281-282)
# When the first date is set and it causes end_datetime to be cleared
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2026, 6, 15)))],
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
    # Set end_datetime in the past relative to the new start date
    meeting.end_datetime = dt.datetime(2025, 1, 1, 12, 0, tzinfo=dt.UTC)
    meeting.lock_on_start = True
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
    # end_datetime was cleared because the new start (23:59 on 2026-06-15 Madrid == 21:59 UTC,
    # summer UTC+2) is after the old end (2025-01-01).
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    # The alert should have been shown
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.END_CLEARED_BY_START.get_text(lang=meeting.lang),
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# fallback_answer — wrong time format (lines 494-501)
# WRONG_TIME_FORMAT and WRONG_TIME_MESSAGE handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="not a time format")],
    indirect=True,
)
async def test_wrong_time_format_fallback_sends_error_and_stays_in_edit_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """WRONG_TIME_FORMAT resends the time prompt (with the wrong-format notice and Cancel button)
    and stays in EDIT_TIME state, so the user is not stranded."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.WRONG_TIME_FORMAT,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_TIME
    context.api.assert_send_message_called(
        update,
        build_edit_time_prompt_view(
            meeting, user_with_settings.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=user_with_settings.lang)
        ),
    )
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongTimeFormat"), value=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(latitude=0, longitude=0))],
    indirect=True,
)
async def test_wrong_time_message_type_fallback_sends_error_and_stays_in_edit_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """WRONG_TIME_MESSAGE (non-text message) resends the time prompt (with the wrong-format notice
    and Cancel button) and stays in EDIT_TIME state, so the user is not stranded."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.WRONG_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_TIME
    context.api.assert_send_message_called(
        update,
        build_edit_time_prompt_view(
            meeting, user_with_settings.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=user_with_settings.lang)
        ),
    )
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongTimeFormat"), value=1)


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
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    expected_datetime = dt.datetime.fromtimestamp(DATE_TIME_ENTITY_UNIX_TIME_FUTURE, tz=dt.UTC)
    assert meeting.datetime == expected_datetime
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    expected_context_message = (
        MeetingEditDateTimeMessages.END_CLEARED_BY_START.get(lang=user_with_settings.lang)
        .append("\n\n")
        .append(
            MeetingEditDateTimeMessages.DATE_UPDATED.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(DATE_TIME_ENTITY_UNIX_TIME_FUTURE),
            )
        )
    )
    context.api.assert_send_message_called(
        update,
        meeting_views.when_view(meeting).with_context(expected_context_message),
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
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_first_time_in_past_shows_alert_and_stays_in_edit_date(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """handle_first_datetime_set: a past first date shows the START_IN_PAST alert and stays in EDIT_DATE."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATE
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_update_to_past_shows_alert_and_stays_in_edit_date(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """handle_datetime_update: moving an existing datetime onto a past date shows the alert and stays in EDIT_DATE."""
    # Existing future datetime; the click keeps its 10:00 time but moves it to the past PAST_DATE.
    existing = dt.datetime(2025, 1, 20, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATE
    assert meeting.datetime == existing  # unchanged
    mock_session.assert_not_flushed()
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(PAST_DATE))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_date_update_to_past_with_naive_stored_datetime_does_not_raise(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """handle_datetime_update tolerates a naive stored meeting.datetime when validating the past start."""
    naive_existing = dt.datetime(2025, 1, 20, 10, 0)  # naive
    assert naive_existing.tzinfo is None
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=naive_existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATE
    assert meeting.datetime == naive_existing  # unchanged, no TypeError raised
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=meeting.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    # date_time entity at 2025-01-10 10:00 UTC — before the frozen now (2025-01-15).
    [datetime_entity_request(int(dt.datetime(2025, 1, 10, 10, 0, tzinfo=dt.UTC).timestamp()))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_date_time_entity_in_past_sends_error_and_stays_in_edit_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """date_time_entity_message_handler: a past entity sends START_IN_PAST and stays in EDIT_DATETIME."""
    existing = dt.datetime(2025, 1, 20, 10, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.datetime == existing  # unchanged
    mock_session.assert_not_flushed()
    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        build_entry_view(
            meeting,
            user_with_settings.lang,
            today,
            error=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=user_with_settings.lang),
        ),
    )


@pytest.mark.parametrize(
    "update",
    # Entity exactly at the frozen now — rejected because the comparison is <=.
    [datetime_entity_request(int(dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.UTC).timestamp()))],
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_date_time_entity_exactly_now_is_rejected(
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
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.datetime == existing  # unchanged
    today = meeting.owner.now_in_tz().date()
    context.api.assert_send_message_called(
        update,
        build_entry_view(
            meeting,
            user_with_settings.lang,
            today,
            error=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=user_with_settings.lang),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="10:00")],  # 10:00 Madrid on a past meeting date → past start
    indirect=True,
)
@freeze_time(START_PAST_FROZEN_NOW, tz_offset=0)
async def test_set_time_in_past_sends_error_and_stays_in_edit_time(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """set_time_message_handler: a time on a past meeting date sends START_IN_PAST and stays in EDIT_TIME."""
    # Meeting date is in the past (PAST_DATE); the handler keeps that date and applies 10:00 Madrid,
    # producing a past start (2025-01-10 09:00 UTC) relative to the frozen now.
    past_meeting = dt.datetime(2025, 1, 10, 8, 0, tzinfo=dt.UTC)
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=past_meeting)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_TIME
    assert meeting.datetime == past_meeting  # unchanged
    mock_session.assert_not_flushed()
    context.api.assert_send_message_called(
        update,
        build_edit_time_prompt_view(
            meeting,
            user_with_settings.lang,
            error=MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=user_with_settings.lang),
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
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(HORIZON_BEYOND_DATE))],
    indirect=True,
)
@freeze_time(HORIZON_FROZEN_NOW, tz_offset=0)
async def test_set_date_beyond_horizon_shows_alert_and_stays_in_edit_date(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A free user picking a date past the horizon gets the upsell alert; the date is not saved."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATE
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    # The rejection replaces the calendar message in place: no alert, the Collaborate button, and
    # a button back to the calendar. It renders in the owner's language (what
    # scheduling_horizon_rejection uses), not the meeting's content language.
    context.api.assert_method_just_called("answer_callback_query", times=0)
    calendar_button = ButtonConfig(
        text=ButtonMessages.DATE.back(lang=user_with_settings.lang),
        callback_data=cb.EDIT_MEETING_DATE.with_id(10).with_date(dt.date(2025, 1, 15)),
    )
    context.api.assert_edit_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON.get_text(lang=user_with_settings.lang, days=31),
            user_with_settings.lang,
        ).with_context_menu([[calendar_button]]),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(HORIZON_BOUNDARY_DATE))],
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
    """The boundary date (exactly the horizon) is accepted: the first date is saved and time prompted."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
    assert meeting.datetime is not None  # saved
    assert meeting.owner.datetime_in_tz(meeting.datetime).date() == HORIZON_BOUNDARY_DATE
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(HORIZON_BEYOND_DATE))],
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

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
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
        EditMeetingHandlerId.SET_TIME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
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
async def test_date_time_entity_beyond_horizon_sends_upsell_and_stays_in_edit_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """date_time_entity_message_handler: a beyond-horizon entity sends the horizon rejection with
    the Collaborate button and stays in EDIT_DATETIME."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_scheduling_horizon_days=31))
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.datetime is None  # not saved
    mock_session.assert_not_flushed()
    entry_back_button = ButtonConfig(
        text=ButtonMessages.DATE_TIME.back(lang=user_with_settings.lang),
        callback_data=cb.EDIT_MEETING.with_id(10),
    )
    context.api.assert_send_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON.get_text(lang=user_with_settings.lang, days=31),
            user_with_settings.lang,
        ).with_context_menu([[entry_back_button]]),
    )


# ---------------------------------------------------------------------------
# START date-first default is 23:59 in the owner timezone (was 00:00).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    # Pick TODAY (frozen to 2024-11-15) as the very first date.
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2024, 11, 15)))],
    indirect=True,
)
@freeze_time("2024-11-15 08:00:00", tz_offset=0)  # 08:00 UTC == 09:00 Madrid, earlier in the day
async def test_set_date_first_time_today_succeeds_with_2359_default(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Regression: picking TODAY as the first date now succeeds. The default of 23:59 (owner-local)
    is still in the future, whereas the old 00:00 default made today <= now and was rejected."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    # No START_IN_PAST rejection: conversation advances to EDIT_TIME.
    assert response == ConversationMeetingState.EDIT_TIME
    # 23:59 on 2024-11-15 Madrid (Nov, UTC+1) == 22:59 UTC the same day.
    assert meeting.datetime == dt.datetime(2024, 11, 15, 22, 59, tzinfo=dt.UTC)
    context.api.assert_method_just_called("answer_callback_query", times=0)


# ---------------------------------------------------------------------------
# handle_datetime_update — DST-safe date-only change preserves owner-LOCAL wall clock.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    # Move the date across the spring DST boundary into summer time.
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2026, 4, 10)))],
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

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATETIME
    # 2026-04-10 is summer time (Madrid UTC+2), so 10:00 local == 08:00 UTC — the UTC time-of-day
    # shifted by one hour relative to the winter value (09:00 UTC).
    assert meeting.datetime == dt.datetime(2026, 4, 10, 8, 0, tzinfo=dt.UTC)
    # The owner-local wall clock is preserved at 10:00.
    assert meeting.datetime is not None
    assert meeting.owner.datetime_in_tz(meeting.datetime).time() == dt.time(10, 0)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2026, 6, 25)))],
    indirect=True,
)
@freeze_time("2026-01-01 00:00:00", tz_offset=0)
async def test_set_date_update_with_naive_stored_datetime_treats_it_as_utc(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A naive stored meeting.datetime is normalised via to_utc before the local-time preservation,
    so it is treated as UTC and never reinterpreted against the system timezone."""
    # Naive value (no tzinfo) — the fix tags it as UTC: 2026-06-20 10:00 UTC.
    naive_existing = dt.datetime(2026, 6, 20, 10, 0)
    assert naive_existing.tzinfo is None
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=naive_existing)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATETIME
    # 10:00 UTC == 12:00 Madrid (summer, UTC+2); preserved on 2026-06-25 that local 12:00 == 10:00 UTC.
    assert meeting.datetime == dt.datetime(2026, 6, 25, 10, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(dt.date(2026, 4, 10)))],
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

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_DATETIME
    # UTC owner: the time-of-day is unchanged, only the date moves.
    assert meeting.datetime == dt.datetime(2026, 4, 10, 9, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# show_edit_time_prompt — time-first prompt note only when meeting.datetime is None.
# Reached via [Time] -> EDIT_TIME_CALLBACK -> callback_query_set_meeting_time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(10))],
    indirect=True,
)
async def test_edit_meeting_time_prompt_without_datetime_shows_default_note(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """With meeting.datetime is None the prompt prepends TIME_DATE_DEFAULT_NOTE before TIME_PROMPT."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.EDIT_TIME_CALLBACK, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
    # show_edit_time_prompt uses meeting.lang (the meeting's own language), not user.lang.
    expected_description = (
        MeetingEditDateTimeMessages.TIME_DATE_DEFAULT_NOTE.get(lang=meeting.lang)
        .append("\n\n")
        .append(CommonMessages.TIME_PROMPT.get(lang=meeting.lang))
    )
    expected_view = MitupView(
        description=expected_description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=meeting.lang),
                    callback_data=cb.CANCEL_EDIT_START_TIME.with_id(10),
                )
            ]
        ],
    )
    context.api.assert_edit_message_called(update, expected_view)
