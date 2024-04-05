import logging
from contextlib import nullcontext
from typing import cast

import pytest
from _pytest.python_api import RaisesContext
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import ContextPropertyNotSetError, MalformedCallbackData, UserNotFound
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.edit_meeting.views import edit_location_view
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.types import StubMitupApp
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import MockApi, UpdateRequest, call_handler
from tests.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_location") as api:
        yield api


def failure_cases(callback_data: CallbackData):
    return [
        (
            UpdateRequest(callback_query=callback_data),
            "user_with_settings",
            pytest.raises(MalformedCallbackData),
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(1)),
            "none",
            pytest.raises(UserNotFound),
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(999)),
            "user_with_settings",
            nullcontext(),
        ),
    ]


def test_edit_location_view(meeting: Meetup):
    meeting_id = cast(int, meeting.id)

    result = edit_location_view(meeting=meeting)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_NAME.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_COORDINATES.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(meeting_id),
                ),
            ],
            [
                ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)),
            ],
        ],
    )

    assert expected_view == result


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION.with_id(1))], indirect=True)
@pytest.mark.asyncio
async def test_callback_edit_location_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(update, app, EditMeetingHandlerId.LOCATION_CALLBACK)

    api.assert_edit_message_called(context, update, edit_location_view(user_with_settings.meetups[0]))


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_LOCATION),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
@pytest.mark.asyncio
async def test_callback_edit_location_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with expectation:
        with caplog.at_level(logging.WARNING):
            with MockApi.start("mitup_bot.guards") as _api:
                context, _ = await call_handler(update, app, EditMeetingHandlerId.LOCATION_CALLBACK)
                # For the test case where we don´t fail but log a warning and go to main menu
                assert "User tried 'Edit location' with a meeting that does not belong to them." in caplog.text
                _api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_NAME.with_id(1))], indirect=True
)
@pytest.mark.asyncio
async def test_callback_edit_location_name_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_NAME_CALLBACK)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION_NAME.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationMeetingState.EDIT_LOCATION_NAME
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_LOCATION_NAME),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
@pytest.mark.asyncio
async def test_callback_edit_location_name_failures(
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with expectation:
        with MockApi.start("mitup_bot.guards") as _api:
            with caplog.at_level(logging.WARNING):
                context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_NAME_CALLBACK)
                # If the meeting id is not found, check we have ended the conversation
                assert result is ConversationHandler.END
                assert "User tried 'Edit location name' with a meeting that does not belong to them." in caplog.text
                _api.assert_edit_message_called(context, update, factory.main_menu_view())

                # Check that meeting id has not been set
                with pytest.raises(ContextPropertyNotSetError):
                    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME):
                        pass


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(1))], indirect=True
)
@pytest.mark.asyncio
async def test_callback_edit_location_coordinates_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION_COORDINATES.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationMeetingState.EDIT_LOCATION_COORDIANTES
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_LOCATION_COORDINATES),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
@pytest.mark.asyncio
async def test_callback_edit_location_coordinates_failures(
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with expectation:
        with MockApi.start("mitup_bot.guards") as _api:
            with caplog.at_level(logging.WARNING):
                context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK)
                # If the meeting id is not found, check we have ended the conversation
                assert result is ConversationHandler.END
                assert (
                    "User tried 'Edit location coordinates' with a meeting that does not belong to them." in caplog.text
                )
                _api.assert_edit_message_called(context, update, factory.main_menu_view())

                # Check that meeting id has not been set
                assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)


@pytest.mark.parametrize("update", [UpdateRequest(message="My Location")], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_name_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        with_meeting_id=(ContextId.EDIT_MEETING_LOCATION_NAME, 1),
    )
    expected_view = edit_location_view(meeting).with_context(
        MeetingMessages.LOCATION_NAME_SET_SUCCESS.get(name=meeting.location.name)
    )

    assert meeting.location.name == "My Location"
    mock_session.assert_flushed()
    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)


@pytest.mark.parametrize("update", [UpdateRequest(message="My Location")], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_name_message_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_NAME_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_coordinates_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        with_meeting_id=(ContextId.EDIT_MEETING_LOCATION_COORDINATES, 1),
    )
    expected_view = edit_location_view(meeting).with_context(MeetingMessages.LOCATION_COORDINATES_SUCCESS.get())

    assert meeting.location.coordinates == (123.4, 567.8)
    mock_session.assert_flushed()
    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_coordinates_message_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.coordinates is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize("update", [UpdateRequest(message="Message instead of coordinates")], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_coordinates_message_with_wrong_message(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
        with_meeting_id=(ContextId.EDIT_MEETING_LOCATION_COORDINATES, 1),
    )

    expected_view = MitupView(
        description=MeetingMessages.LOCATION_COORDINATES_WRONG.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    assert result is ConversationMeetingState.EDIT_LOCATION_COORDIANTES
    api.assert_send_message_called(context, update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(message="Message instead of coordinates")], indirect=True)
@pytest.mark.asyncio
async def test_edit_location_coordinates_message_with_wrong_message_fails_without_context(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view())
