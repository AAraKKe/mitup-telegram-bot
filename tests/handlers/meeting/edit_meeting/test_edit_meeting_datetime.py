import datetime as dt
from typing import cast

import pytest
from aws_embedded_metrics.unit import Unit
from freezegun import freeze_time
from telegram import CallbackQuery, Location, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Meetup, Message, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, MockApi, StubMitupApp, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession

TEST_MEETING_DATETIME_UTC = dt.datetime(2024, 12, 21, 12, 0, tzinfo=dt.UTC)
TEST_CURRENT_DATE = dt.date(2024, 11, 15)


def set_new_date_view(meeting_id: int, datetime: str) -> MitupView:
    return MitupView(
        description=MeetingMessages.NEW_DATE_SET_SUCCESS.get(
            datetime=datetime,
            back_edit_button=ButtonMessages.BACK_EDIT.get(),
            set_time_button=ButtonMessages.SET_TIME.get(),
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.SET_TIME.get(), callback_data=cb.EDIT_MEETING_TIME.with_id(meeting_id)
                ),
                ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)),
            ]
        ],
    )


def update_meeting_view(meeting: Meetup, datetime: str) -> MitupView:
    return meeting.edit_view.with_context(MeetingMessages.DATE_UPDATE_SUCCESS.get(datetime=datetime))


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        yield api


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
    ],
    indirect=["update"],
    ids=["meeting_dt_set", "meeting_dt_not_set", "meeting_dt_set_different_current_date"],
)
async def test_edit_meeting_date_callback(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    anchor_date: dt.date,
    current_date: dt.date,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(update, app, EditMeetingHandlerId.DATE_CALLBACK)

    api.assert_edit_message_called(
        context,
        update,
        factory.edit_meeting_date_view(10, anchor_date, current_date, new=meeting.datetime is None),
    )


@pytest.mark.parametrize(
    "update,meeting,new",
    [
        (
            UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            create_meetup(id=10, title="TestMeeting", description="Description"),
            True,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(10).with_date(TEST_MEETING_DATETIME_UTC.date())),
            create_meetup(
                id=10,
                title="TestMeeting",
                description="Description",
                datetime=dt.datetime(2024, 11, 11, 12, 30, tzinfo=dt.UTC),
            ),
            False,
        ),
    ],
    indirect=["update"],
    ids=["set_date_for_the_first_time", "update_existing_date"],
)
async def test_set_meeting_date_callback(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    new: bool,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Lets add a message to validate it has been updated
    Message(message_id=111, chat_id=111, meetup=meeting)

    context, _ = await call_handler(update, app, EditMeetingHandlerId.SET_DATE_CALLBACK)

    expected_view = (
        set_new_date_view(10, "2024-12-21 23:59 (Europe/Madrid)")
        if new
        else update_meeting_view(meeting, "2024-12-21 13:30 (Europe/Madrid)")
    )
    expected_datetime = (
        # The meeting is set to 23:59 on the user timezone, i.e. one hour earlier in UTC
        dt.datetime.combine(TEST_MEETING_DATETIME_UTC.date(), dt.time(22, 59, tzinfo=dt.UTC))
        if new
        # If the meeting already has a time, it should be updated to the new date keeping the time
        else dt.datetime.combine(TEST_MEETING_DATETIME_UTC.date(), dt.time(12, 30, tzinfo=dt.UTC))
    )

    assert meeting.datetime == expected_datetime
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()
    api.assert_edit_message_called(
        context,
        update,
        expected_view,
    )
    api.assert_update_meeting_messages_called(mock_session, context, meeting, None, True)


@pytest.mark.parametrize(
    "update,meeting",
    [
        (
            UpdateRequest(callback_query=cb.DELETE_MEETING_DATE.with_id(10)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
        ),
    ],
    indirect=["update"],
    ids=["delete_meeting_date"],
)
async def test_delete_meeting_date(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(update, app, EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK)

    assert meeting.datetime is None
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()
    api.assert_edit_message_called(
        context, update, meeting.edit_view.with_context(MeetingMessages.DATE_TIME_DELETED.get())
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
    app: StubMitupApp,
):
    if meeting.id == 10:
        user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        context, response = await call_handler(update, app, EditMeetingHandlerId.EDIT_TIME_CALLBACK)

    assert response == expected_response
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME) or expected_response == ConversationHandler.END

    expected_view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(10),
                )
            ]
        ],
    )

    if expected_response == ConversationMeetingState.EDIT_TIME:
        api.assert_edit_message_called(context, update, expected_view)

    # StoredMeetingId is emitted only if the meeting is accessible
    names = [MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), MetricKey.FAULT.value, MetricKey.TIME.value]
    values = [1 if expected_response == ConversationHandler.END else 0, 0, AnyFloat()]
    units = [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]
    properties = None
    if expected_response == ConversationMeetingState.EDIT_TIME:
        names.append("StoredMeetingId")
        values.append(1)
        units.append(Unit.COUNT)
        properties = {"ContextId": ContextId.EDIT_MEETING_TIME.value}

    context.metrics_engine.assert_metrics_emited(
        names=names,
        values=values,
        units=units,
        properties=properties,
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update, meeting, expected_meeting_time,expected_time_displayed",
    [
        (
            UpdateRequest(message_text="20:20"),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
            dt.datetime(2024, 12, 21, 19, 20, tzinfo=dt.UTC),
            "2024-12-21 20:20 (Europe/Madrid)",
        ),
        (
            UpdateRequest(message_text="20:20"),
            create_meetup(id=10, title="TestMeeting", description="Description"),
            dt.datetime(2025, 1, 1, 19, 20, tzinfo=dt.UTC),
            "2025-01-01 20:20 (Europe/Madrid)",
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
    expected_time_displayed: str,
    user_with_settings: User,
    app: StubMitupApp,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        context, response = await call_handler(
            update, app, EditMeetingHandlerId.SET_TIME_MESSAGE, with_meeting_id={ContextId.EDIT_MEETING_TIME: 10}
        )

    assert response == ConversationHandler.END
    # Since the user provides 20:20 in Europ/Madrid, the UTC time stored in the meeting is one hour earlier
    assert meeting.datetime == expected_meeting_time
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    # Meeting id has been removed from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    context.metrics_engine.assert_metrics_emited(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            "CleanUserData",
            MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED),
        ],
        values=[AnyFloat(), 0, 1, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
        add_handler_dimensions=True,
        add_update_properties=True,
    )

    api.assert_send_message_called(
        context,
        update,
        meeting.edit_view.with_context(MeetingMessages.EDIT_TIME_SUCCESS.get(datetime=expected_time_displayed)),
    )
    api.assert_update_meeting_messages_called(mock_session, context, meeting)


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
    app: StubMitupApp,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        context, response = await call_handler(
            update, app, EditMeetingHandlerId.SET_TIME_MESSAGE, with_meeting_id={ContextId.EDIT_MEETING_TIME: 10}
        )

    assert response == ConversationMeetingState.EDIT_TIME
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Message sent to retry
    api.assert_send_message_called(context, update, MeetingMessages.INVALID_TIME.get())

    context.metrics_engine.assert_handler_metrics_emitted(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            MetricKey.ERROR.with_prefix("InvalidTime"),
        ],
        values=[AnyFloat(), 0, 1],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT],
    )


def entry_point_update(update: Update):
    return Update(
        123,
        callback_query=CallbackQuery(
            id="123",
            from_user=cast(TgUser, update.effective_user),
            message=update.effective_message,
            data=str(cb.EDIT_MEETING_TIME.with_id(10)),
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
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        # Lets first trigger the conversation
        context, _ = await call_handler(
            entry_point_update(update),
            app,
            EditMeetingHandlerId.EDIT_TIME_CONVERSATION,
            with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
        )

        # Now answer with a wrong message format
        context, _ = await call_handler(update, app, EditMeetingHandlerId.EDIT_TIME_CONVERSATION)

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Message sent to retry
    api.assert_send_message_called(context, update, MeetingMessages.WRONG_TIME_FORMAT.get())

    context.metrics_engine.assert_handler_metrics_emitted(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            MetricKey.ERROR.with_prefix("WrongTimeFormat"),
        ],
        values=[AnyFloat(), 0, 1],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT],
    )


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(10)))],
    indirect=True,
)
async def test_edit_time_can_be_cancelled(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_datetime") as api:
        context, _ = await call_handler(
            entry_point_update(update),
            app,
            EditMeetingHandlerId.EDIT_TIME_CONVERSATION,
            with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
        )
        context, _ = await call_handler(update, app, EditMeetingHandlerId.EDIT_TIME_CONVERSATION)

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    api.assert_edit_message_called(context, update, meeting.edit_view, times=2)
