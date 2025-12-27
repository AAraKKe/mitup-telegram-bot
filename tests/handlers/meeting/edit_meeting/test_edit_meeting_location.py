import logging

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.edit_meeting.views import edit_location_view
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, StubMitupApp, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


def failure_cases(callback_data: CallbackData):
    return [
        (
            UpdateRequest(callback_query=callback_data),
            "user_with_settings",
            MalformedCallbackData,
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(1)),
            "none",
            UserNotFound,
        ),
    ]


def assert_metrics_for_failure(error_count: int, error_type: type[Exception], context: StubMitupContext):
    expected_metric_names: list[str | MetricKey] = [
        MetricKey.FAULT.with_prefix(error_type.__name__),
        MetricKey.FAULT,
        MetricKey.TIME,
        MetricKey.DB_CONNECTIONS_LEAKED,
    ]
    expected_metric_values = [error_count, error_count, AnyFloat(), 0]
    expected_metric_units = [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT]

    context.metrics_engine.assert_handler_metrics_emitted(
        expected_metric_names,
        expected_metric_values,
        units=expected_metric_units,
        exception=error_type,
    )


def test_edit_location_view(meeting: Meetup, lang: str):
    meeting_id = meeting.db_id
    meeting.language = lang

    result = edit_location_view(meeting=meeting)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_NAME.get(lang=lang),
                    callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_COORDINATES.get(lang=lang),
                    callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(meeting_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
                ),
            ],
        ],
    )

    assert expected_view == result


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION.with_id(1))], indirect=True)
async def test_edit_location_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, update=update, app=app)

    context.api.assert_edit_message_called(update, edit_location_view(user_with_settings.meetups[0]))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION.with_id(999))], indirect=True)
async def test_edit_location_meeting_not_owned(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, update=update, app=app)
        # For the test case where we don´t fail but log a warning and go to main menu
        assert "User tried 'Edit location' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))

    context.metrics_engine.assert_metrics_emited(
        [
            MetricKey.ERROR.with_prefix("MeetingNotOwned"),
            MetricKey.FAULT,
            MetricKey.TIME,
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        [1, 0, AnyFloat(), 0],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.EDIT_MEETING_LOCATION),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_location_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, update=update, app=app)

    assert_metrics_for_failure(1, error_type, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_NAME.with_id(1))], indirect=True
)
async def test_edit_location_name_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, update=update, app=app)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION_NAME.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationMeetingState.EDIT_LOCATION_NAME
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_NAME.with_id(999))], indirect=True
)
async def test_edit_location_name_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, update=update, app=app)
        # If the meeting id is not found, check we have ended the conversation
        assert result is ConversationHandler.END
        assert "User tried 'Edit location name' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    context.metrics_engine.assert_metrics_emited(
        [
            MetricKey.ERROR.with_prefix("MeetingNotOwned"),
            MetricKey.FAULT,
            MetricKey.TIME,
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        [1, 0, AnyFloat(), 0],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.EDIT_MEETING_LOCATION_NAME),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_location_name_failures(
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    app: StubMitupApp,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, update=update, app=app)

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    assert_metrics_for_failure(1, error_type, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(1))], indirect=True
)
async def test_edit_location_coordinates_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, update=update, app=app)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION_COORDINATES.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationMeetingState.EDIT_LOCATION_COORDIANTES
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(999))], indirect=True
)
async def test_edit_location_coordinates_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, update=update, app=app)
        # If the meeting id is not found, check we have ended the conversation
        assert result is ConversationHandler.END
        assert "User tried 'Edit location coordinates' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    context.metrics_engine.assert_metrics_emited(
        [
            MetricKey.ERROR.with_prefix("MeetingNotOwned"),
            MetricKey.FAULT,
            MetricKey.TIME,
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        [1, 0, AnyFloat(), 0],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.EDIT_MEETING_LOCATION_COORDINATES),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_location_coordinates_failures(
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    app: StubMitupApp,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, update=update, app=app)

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    assert_metrics_for_failure(1, error_type, context)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="My Location")], indirect=True)
async def test_edit_location_name_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 1},
    )
    expected_view = edit_location_view(meeting).with_context(
        MeetingMessages.LOCATION_NAME_SET_SUCCESS.get(name=meeting.location.name)
    )

    assert meeting.location.name == "My Location"
    mock_session.assert_flushed()
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="My Location")], indirect=True)
async def test_edit_location_name_message_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(EditMeetingHandlerId.LOCATION_NAME_MESSAGE, update=update, app=app)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
async def test_edit_location_coordinates_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 1},
    )
    expected_view = edit_location_view(meeting).with_context(
        MeetingMessages.LOCATION_COORDINATES_SUCCESS.get(lang=user_with_settings.lang)
    )

    assert meeting.location.coordinates == (123.4, 567.8)
    mock_session.assert_flushed()
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
async def test_edit_location_coordinates_message_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, update=update, app=app)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.coordinates is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize("update", [UpdateRequest(message_text="Message instead of coordinates")], indirect=True)
async def test_edit_location_coordinates_message_with_wrong_message(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 1},
    )

    expected_view = MitupView(
        description=MeetingMessages.LOCATION_COORDINATES_WRONG.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    assert context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)
    assert result is ConversationMeetingState.EDIT_LOCATION_COORDIANTES
    context.api.assert_send_message_called(update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="Message instead of coordinates")], indirect=True)
async def test_edit_location_coordinates_message_with_wrong_message_fails_without_context(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE, update=update, app=app
        )
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1))], indirect=True
)
async def test_cancel_edit_meeting_location_property_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK, update=update, app=app)

    context.api.assert_edit_message_called(update, edit_location_view(user_with_settings.meetups[0]))
    assert result is ConversationHandler.END
