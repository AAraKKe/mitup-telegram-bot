import datetime as dt
from typing import cast

import pytest
from aws_embedded_metrics.unit import Unit
from freezegun import freeze_time
from telegram import CallbackQuery, Chat, Location, Message, Update
from telegram import User as TgUser
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import UserNotFound
from mitup_bot.handlers.edit_meeting.edit_meeting_datetime import build_edit_datetime_entry_view as _build_entry_view
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MeetupMessage
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils import render
from mitup_bot.utils.entities import DateTimeMessageEntity, EntityDateTime, FormattedText, build_datetime_link
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, StubMitupApp, UpdateRequest, call_handler, create_meetup
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID, DEFAULT_TEST_DATE, DEFAULT_TG_USER_PARAMS
from tests.helpers.stub_db import MockDbSession

TEST_MEETING_DATETIME_UTC = dt.datetime(2024, 12, 21, 12, 0, tzinfo=dt.UTC)
TEST_CURRENT_DATE = dt.date(2024, 11, 15)
TEST_31ST_DATETIME = dt.datetime(2024, 10, 31, 12, 30, 0, tzinfo=dt.UTC)


def set_new_date_view(lang: str, meeting_id: int, datetime: FormattedText) -> MitupView:
    # Production code shows a single Done button (cb.EDIT_MEETING_CANCEL) after the first date is set.
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
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
                ),
            ]
        ],
    )


def update_meeting_view(meeting: Meetup, datetime: FormattedText) -> MitupView:
    return meeting.edit_view.with_context(MeetingMessages.DATE_UPDATE_SUCCESS.get(lang=meeting.lang, datetime=datetime))


def datetime_entity(unix_time: int) -> FormattedText:
    """Build the FormattedText that the handler produces for a set datetime."""
    return render(t"{EntityDateTime('Meeting time', unix_time=unix_time, date_time_format='DT')}")


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
    app: StubMitupApp,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.DATE_CALLBACK, update=update, app=app)

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
    app: StubMitupApp,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=current_datetime)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Lets add a message to validate it has been updated
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(EditMeetingHandlerId.SET_DATE_CALLBACK, update=update, app=app)

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
        dt_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), int(expected_datetime.timestamp()), "DT")
        expected_view = _build_entry_view(meeting, meeting.lang, today).with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(
                lang=meeting.lang,
                datetime=render(t"{dt_entity}"),
            )
        )
        context.api.assert_edit_message_called(update, expected_view)
        context.api.assert_update_meeting_messages_called(mock_session, meeting, None, True)


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
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK, update=update, app=app)

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingMessages.DELETE_DATE_CONFIRMATION.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_DATE.with_id(10),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_DATE.with_id(10),
        ),
    )
    context.api.assert_update_meeting_messages_not_called()


@pytest.mark.parametrize(
    "update,meeting",
    [
        (
            UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_DATE.with_id(10)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
        ),
    ],
    indirect=["update"],
    ids=["confirm_delete_meeting_date"],
)
async def test_confirm_delete_meeting_date(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    user_with_settings: User,
    app: StubMitupApp,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    assert meeting.datetime is None
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()
    # cleanup_states clears EDIT_MEETING_TIME from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)
    context.api.assert_edit_message_called(
        update,
        meeting.edit_view.with_context(MeetingMessages.DATE_TIME_DELETED.get(lang=user_with_settings.lang)),
    )
    context.api.assert_update_meeting_messages_called(mock_session, meeting, None, True)


@pytest.mark.parametrize(
    "update,meeting",
    [
        (
            UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_DATE.with_id(10)),
            create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC),
        ),
    ],
    indirect=["update"],
    ids=["decline_delete_meeting_date"],
)
async def test_decline_delete_meeting_date(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    user_with_settings: User,
    app: StubMitupApp,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK, update=update, app=app
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()
    # Use meeting.owner.now_in_tz().date() to get the same FakeDate that the handler computes under freeze_time
    today = meeting.owner.now_in_tz().date()
    context.api.assert_edit_message_called(
        update,
        _build_entry_view(meeting, user_with_settings.lang, today).with_context(
            MeetingMessages.DELETE_DATE_DECLINE.get(lang=user_with_settings.lang)
        ),
    )
    context.api.assert_update_meeting_messages_not_called()


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
    if meeting.db_id == 10:
        user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.EDIT_TIME_CALLBACK, update=update, app=app)

    assert response == expected_response
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME) or expected_response == ConversationHandler.END

    # show_edit_time_prompt uses meeting.lang (the meeting's own language), not user.lang
    expected_view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(lang=meeting.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=meeting.lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(10),
                )
            ]
        ],
    )

    if expected_response == ConversationMeetingState.EDIT_TIME:
        context.api.assert_edit_message_called(update, expected_view)

    # StoredMeetingId is emitted only if the meeting is accessible
    names = [
        MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED),
        MetricKey.FAULT.value,
        MetricKey.TIME.value,
        MetricKey.DB_CONNECTIONS_LEAKED.value,
    ]
    values = [1 if expected_response == ConversationHandler.END else 0, 0, AnyFloat(), 0]
    units = [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT]
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
    app: StubMitupApp,
):
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.SET_TIME_MESSAGE, update=update, app=app, with_meeting_id={ContextId.EDIT_MEETING_TIME: 10}
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
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        values=[AnyFloat(), 0, 1, 0, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
        add_handler_dimensions=True,
        add_update_properties=True,
    )

    context.api.assert_send_message_called(
        update,
        meeting.edit_view.with_context(
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
    app: StubMitupApp,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.SET_TIME_MESSAGE, update=update, app=app, with_meeting_id={ContextId.EDIT_MEETING_TIME: 10}
    )

    assert response == ConversationMeetingState.EDIT_TIME
    assert meeting.datetime == TEST_MEETING_DATETIME_UTC
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Message sent to retry
    context.api.assert_send_message_called(update, MeetingMessages.INVALID_TIME.get(lang=user_with_settings.lang))

    context.metrics_engine.assert_handler_metrics_emitted(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            MetricKey.ERROR.with_prefix("InvalidTime"),
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        values=[AnyFloat(), 0, 1, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT],
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

    # Lets first trigger the conversation
    context, _ = await call_handler(
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        update=entry_point_update(update),
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    # Now answer with a wrong message format
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, update=update, app=app)

    # Meeting id still in context
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    # Message sent to retry
    context.api.assert_send_message_called(
        update, MeetingMessages.WRONG_TIME_FORMAT.get(lang=user_with_settings.lang), times=1
    )

    context.metrics_engine.assert_handler_metrics_emitted(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            MetricKey.ERROR.with_prefix("WrongTimeFormat"),
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        values=[AnyFloat(), 0, 1, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT],
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

    context, _ = await call_handler(
        EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION,
        update=entry_point_update(update),
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )
    context, _ = await call_handler(EditMeetingHandlerId.EDIT_DATETIME_CONVERSATION, update=update, app=app)

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)

    context.api.assert_edit_message_called(update, meeting.edit_view, times=1)


def date_time_entity_update(user: TgUser, unix_time: int) -> Update:
    """Build an Update containing a message with a ``date_time`` entity."""
    chat = Chat(id=DEFAULT_CHAT_ID, type="private")
    text = "Tomorrow at noon"
    entity = DateTimeMessageEntity(offset=0, length=len(text), unix_time=unix_time)
    message = Message(
        DEFAULT_MESSAGE_ID,
        date=DEFAULT_TEST_DATE,
        chat=chat,
        from_user=user,
        text=text,
        entities=[entity],
    )
    return Update(DEFAULT_MESSAGE_ID, message=message)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.EDIT_MEETING_DATE_TIME.with_id(10)))],
    indirect=True,
)
async def test_date_time_entry_callback(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK, update=update, app=app)

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
            # Delete row is shown because meeting.datetime is set
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_DATE.get(lang=user_with_settings.lang),
                    callback_data=cb.DELETE_MEETING_DATE.with_id(10),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.back(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING.with_id(10),
                ),
            ],
        ],
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.EDIT_MEETING_DATE_TIME.with_id(10)))],
    indirect=["update"],
    ids=["date_time_entry_no_datetime"],
)
async def test_date_time_entry_callback_without_datetime(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    """DATE_TIME_ENTRY_CALLBACK with meeting.datetime=None must NOT include the DELETE_DATE button."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    # Explicitly ensure no datetime is set (create_meetup defaults to None already)
    assert meeting.datetime is None
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK, update=update, app=app)

    assert response == ConversationMeetingState.EDIT_DATETIME

    today = user_with_settings.now_in_tz().date()
    datetime_link = build_datetime_link()
    # When meeting.datetime is None the DELETE_DATE row (branch 152→161) is skipped
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
            # No DELETE_DATE row — meeting.datetime is None
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.back(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING.with_id(10),
                ),
            ],
        ],
    )
    context.api.assert_edit_message_called(update, expected_view)


async def test_date_time_entity_message(
    mock_session: MockDbSession,
    user_with_settings: User,
    app: StubMitupApp,
):
    unix_time = 1735000000  # arbitrary fixed unix timestamp for assertions
    tg_user = TgUser(**DEFAULT_TG_USER_PARAMS)
    update = date_time_entity_update(tg_user, unix_time)

    meeting = create_meetup(id=10, title="TestMeeting", description="Description")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    MeetupMessage(message_id=111, chat_id=111, meetup=meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    expected_datetime = dt.datetime.fromtimestamp(unix_time, tz=dt.UTC)
    assert meeting.datetime == expected_datetime
    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    context.api.assert_send_message_called(
        update,
        meeting.edit_view.with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(
                lang=user_with_settings.lang,
                datetime=datetime_entity(unix_time),
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
    app: StubMitupApp,
):
    """Plain text and non-text messages in EDIT_DATETIME state return an error and stay in EDIT_DATETIME."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(handler_id, update=update, app=app)

    assert response == expected_state
    context.api.assert_send_message_called(
        update,
        MeetingMessages.WRONG_DATETIME_MESSAGE.get(lang=user_with_settings.lang, datetime_link=build_datetime_link()),
    )
    context.metrics_engine.assert_handler_metrics_emitted(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT,
            MetricKey.ERROR.with_prefix("WrongDatetimeFormat"),
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        values=[AnyFloat(), 0, 1, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT],
    )


async def test_date_time_entity_message_user_not_found(
    mock_session: MockDbSession,
    user_with_settings: User,
    app: StubMitupApp,
):
    """DATE_TIME_ENTITY_MESSAGE raises UserNotFound when the user is not registered."""
    tg_user = TgUser(**DEFAULT_TG_USER_PARAMS)
    update = date_time_entity_update(tg_user, unix_time=1735000000)
    # Do not add the user to the session — guard raises UserNotFound.

    context, _ = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    )

    context.metrics_engine.assert_metrics_emited(
        names=[
            MetricKey.TIME,
            MetricKey.FAULT.with_prefix("UserNotFound"),
            MetricKey.FAULT,
            "CleanUserData",
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        values=[AnyFloat(), 1, 1, 1, 0],
        units=[Unit.MILLISECONDS, Unit.COUNT, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        exception=UserNotFound,
        properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
        add_handler_dimensions=True,
        add_update_properties=True,
    )


async def test_date_time_entity_message_meeting_not_owned(
    mock_session: MockDbSession,
    user_with_settings: User,
    app: StubMitupApp,
):
    """DATE_TIME_ENTITY_MESSAGE stops when the meeting is not accessible to the user."""
    tg_user = TgUser(**DEFAULT_TG_USER_PARAMS)
    update = date_time_entity_update(tg_user, unix_time=1735000000)
    not_owned_meeting = create_meetup(id=99, title="Not Owned")
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(not_owned_meeting)

    context, response = await call_handler(
        EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    )

    assert response == ConversationHandler.END

    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(10)))],
    indirect=["update"],
    ids=["back_to_edit_meeting"],
)
async def test_back_to_edit_meeting(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    """BACK_TO_EDIT_MEETING_CALLBACK cleans up context and returns ConversationHandler.END."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.BACK_TO_EDIT_MEETING_CALLBACK,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_TIME: 10},
    )

    assert response == ConversationHandler.END
    # cleanup_states must have removed EDIT_MEETING_TIME from context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_TIME)
    context.api.assert_edit_message_called(update, meeting.edit_view)


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
    app: StubMitupApp,
):
    """BACK_TO_EDIT_DATETIME_CALLBACK re-shows the EDIT_DATETIME entry view and returns EDIT_DATETIME."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", datetime=TEST_MEETING_DATETIME_UTC)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, response = await call_handler(
        EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        update=update,
        app=app,
    )

    assert response == ConversationMeetingState.EDIT_DATETIME
    # Use meeting.owner.now_in_tz().date() to get the same FakeDate that the handler produces under freeze_time
    today = meeting.owner.now_in_tz().date()
    context.api.assert_edit_message_called(update, _build_entry_view(meeting, user_with_settings.lang, today))
