import datetime as dt
from typing import cast

import pytest
from freezegun import freeze_time
from telegram import CallbackQuery, Location, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.edit_meeting_datetime import build_edit_datetime_entry_view as _build_entry_view
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MeetupMessage
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit, NullBackend
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils import render
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, HandlerContext, StubMitupApp, UpdateRequest, call_handler, create_meetup
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

TEST_MEETING_DATETIME_UTC = dt.datetime(2024, 12, 21, 12, 0, tzinfo=dt.UTC)
TEST_CURRENT_DATE = dt.date(2024, 11, 15)
TEST_31ST_DATETIME = dt.datetime(2024, 10, 31, 12, 30, 0, tzinfo=dt.UTC)


def set_new_date_view(lang: str, meeting_id: int, datetime: FormattedText) -> MitupView:
    # Production code shows a single Done button (cb.CANCEL_EDIT_START_TIME) after the first date is set.
    # The message has only a ${datetime} placeholder — no ${done_button}.
    return MitupView(
        description=MeetingMessages.NEW_DATE_SET_SUCCESS.get(
            lang=lang,
            datetime=datetime,
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get(lang=lang),
                    callback_data=cb.CANCEL_EDIT_START_TIME.with_id(meeting_id),
                ),
            ]
        ],
    )


def update_meeting_view(meeting: Meetup, datetime: FormattedText) -> MitupView:
    return meeting.edit_view.with_context(MeetingMessages.DATE_UPDATE_SUCCESS.get(lang=meeting.lang, datetime=datetime))


def datetime_entity(unix_time: int) -> FormattedText:
    """Build the FormattedText that the handler produces for a set datetime."""
    unix_dt = dt.datetime.fromtimestamp(unix_time, tz=dt.UTC)
    return render(t"{EntityDateTime('Meeting time', unix_time=unix_dt, date_time_format='DT')}")


@pytest.fixture(autouse=True)
def freeze_current_date():
    """Allow all calls to now or today to return the same date."""
    with freeze_time(TEST_CURRENT_DATE.strftime("%Y-%m-%d")):
        yield


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
            lang=user_with_settings.lang,
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
        # The meeting is set to 00:00 on the user timezone (Europe/Madrid, UTC+1 in December),
        # which is 23:00 UTC on the previous day (2024-12-20).
        dt.datetime(2024, 12, 20, 23, 0, tzinfo=dt.UTC)
        if new
        # If the meeting already has a time, it should be updated to the new date keeping the time
        else dt.datetime.combine(TEST_MEETING_DATETIME_UTC.date(), dt.time(12, 30, tzinfo=dt.UTC))
    )

    assert meeting.datetime == expected_datetime
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

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
        context.api.assert_update_meeting_messages_called(mock_session, meeting, None, True)
    else:
        # Updating an existing datetime: re-show entry view with success context, return EDIT_DATETIME.
        # The handler uses meeting.lang (the meeting's own language, "en" from create_meetup), not the
        # user's session language — so both en and es_ES user variants produce the same English view.
        # today must be obtained under freeze_time to match the FakeDate from meeting.owner.now_in_tz().
        assert response == ConversationMeetingState.EDIT_DATETIME
        today = meeting.owner.now_in_tz().date()
        dt_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), expected_datetime, "DT")
        expected_view = _build_entry_view(meeting, meeting.lang, today).with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(
                lang=meeting.lang,
                datetime=render(t"{dt_entity}"),
            )
        )
        context.api.assert_edit_message_called(update, expected_view)
        context.api.assert_update_meeting_messages_called(mock_session, meeting, None, True)


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
        text=MeetingMessages.END_DATETIME_CLEARED_BY_START.get_text(lang=meeting.lang),
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
        description=MeetingMessages.EDIT_TIME.get(lang=meeting.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=meeting.lang),
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
        metrics.assert_emitted(
            name="StoredMeetingId", value=1, properties={"ContextId": ContextId.EDIT_MEETING_TIME.value}
        )
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


@pytest.mark.parametrize(
    "update, meeting, expected_meeting_time",
    [
        (
            UpdateRequest(message_text="20:20"),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            dt.datetime(2024, 12, 21, 19, 20, tzinfo=dt.UTC),
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
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    # Meeting id has been removed from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=2)
    metrics.assert_emitted(name="CleanUserData", value=1)
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=0)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)

    context.api.assert_send_message_called(
        update,
        meeting.when_view.with_context(
            MeetingMessages.EDIT_TIME_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(int(expected_meeting_time.timestamp())),
            )
        ),
    )
    context.api.assert_update_meeting_messages_called(mock_session, meeting)


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

    # Message sent to retry
    context.api.assert_send_message_called(update, MeetingMessages.INVALID_TIME.get(lang=user_with_settings.lang))

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("InvalidTime"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


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
    ctx = HandlerContext(update=entry_point_update(update), app=app, metrics_client=MetricsClient(NullBackend()))
    context, _ = await call_handler(
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    # Now answer with a wrong message format (in EDIT_DATETIME state, the fallback is WRONG_DATETIME_MESSAGE)
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, handler_context=handler_context)

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Message sent to retry (EDIT_DATETIME state fallback)
    context.api.assert_send_message_called(
        update,
        MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=user_with_settings.lang, datetime_link=build_datetime_link()),
        times=1,
    )

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


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

    ctx = HandlerContext(update=entry_point_update(update), app=app, metrics_client=MetricsClient(NullBackend()))
    context, _ = await call_handler(
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        handler_context=ctx,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, handler_context=handler_context)

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    context.api.assert_edit_message_called(update, meeting.when_view, times=1)


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
        description=MeetingMessages.DATE_TIME_VIEW_MESSAGE.get(
            lang=user_with_settings.lang, datetime_link=datetime_link
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.get(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_DATE.with_id(10).with_date(today),
                ),
                ButtonConfig(
                    text=ButtonMessages.TIME.get(lang=user_with_settings.lang),
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
        description=MeetingMessages.DATE_TIME_VIEW_MESSAGE.get(
            lang=user_with_settings.lang, datetime_link=datetime_link
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.get(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_DATE.with_id(10).with_date(today),
                ),
                ButtonConfig(
                    text=ButtonMessages.TIME.get(lang=user_with_settings.lang),
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
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    context.api.assert_send_message_called(
        update,
        meeting.when_view.with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(DATE_TIME_ENTITY_UNIX_TIME),
            )
        ),
    )
    context.api.assert_update_meeting_messages_called(mock_session, meeting)


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
    """Plain text and non-text messages in EDIT_DATETIME state return an error and stay in EDIT_DATETIME."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(handler_id, handler_context=handler_context)

    assert response == expected_state
    context.api.assert_send_message_called(
        update,
        MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=user_with_settings.lang, datetime_link=build_datetime_link()),
    )
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("WrongDatetimeFormat"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


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

    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("UserNotFound"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)
    metrics.assert_emitted(name="CleanUserData", value=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


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

    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


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
    context.api.assert_edit_message_called(update, meeting.when_view)


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
    context.api.assert_edit_message_called(update, _build_entry_view(meeting, user_with_settings.lang, today))


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
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
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

    # The user sets 20:20 in Europe/Madrid (UTC+1), stored as 19:20 UTC on 2025-01-01 (freeze_time date).
    # 2025-01-01 19:20 UTC > 2024-12-21 18:00 UTC, so end_datetime is cleared.
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    # The context message should include END_DATETIME_CLEARED_BY_START prepended
    assert meeting.datetime is not None
    expected_time_entity = datetime_entity(int(meeting.datetime.timestamp()))
    expected_context_message = (
        MeetingMessages.END_DATETIME_CLEARED_BY_START.get(lang=user_with_settings.lang)
        .append("\n\n")
        .append(
            MeetingMessages.EDIT_TIME_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=expected_time_entity,
            )
        )
    )
    context.api.assert_send_message_called(
        update,
        meeting.when_view.with_context(expected_context_message),
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
    # end_datetime was cleared because new start (2026-06-14 23:00 UTC in Europe/Madrid) > end (2025-01-01)
    assert meeting.end_datetime is None
    assert meeting.lock_on_start is False

    # The alert should have been shown
    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.END_DATETIME_CLEARED_BY_START.get_text(lang=meeting.lang),
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
    """WRONG_TIME_FORMAT sends wrong time format error and stays in EDIT_TIME state."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.WRONG_TIME_FORMAT, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
    context.api.assert_send_message_called(update, MeetingMessages.WRONG_TIME_FORMAT.get(lang=user_with_settings.lang))
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
    """WRONG_TIME_MESSAGE (non-text message) sends wrong time format error and stays in EDIT_TIME state."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.WRONG_TIME_MESSAGE, handler_context=handler_context)

    assert response == ConversationMeetingState.EDIT_TIME
    context.api.assert_send_message_called(update, MeetingMessages.WRONG_TIME_FORMAT.get(lang=user_with_settings.lang))
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
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    expected_context_message = (
        MeetingMessages.END_DATETIME_CLEARED_BY_START.get(lang=user_with_settings.lang)
        .append("\n\n")
        .append(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(DATE_TIME_ENTITY_UNIX_TIME_FUTURE),
            )
        )
    )
    context.api.assert_send_message_called(
        update,
        meeting.when_view.with_context(expected_context_message),
    )
    context.api.assert_update_meeting_messages_called(mock_session, meeting)
