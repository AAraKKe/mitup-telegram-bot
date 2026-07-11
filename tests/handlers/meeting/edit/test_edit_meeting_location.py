import logging

import pytest
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.views import edit_location_view
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditLocationMessages
from mitup_bot.views import MitupView, RenderContext, factory
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    UpdateRequest,
    call_handler,
    create_meetup,
    owner_with_meeting,
)
from tests.helpers.monitoring import MetricAssertions
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


def assert_metrics_for_failure(error_count: int, error_type: type[Exception], metrics_client: MetricsClient):
    metrics = MetricAssertions(metrics_client)
    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix(error_type.__name__), value=error_count)
    metrics.assert_emitted(name=MetricKey.FAULT, value=error_count, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


def test_edit_location_view(meeting: Meetup, lang: str):
    meeting_id = meeting.db_id
    meeting.language = lang

    result = edit_location_view(meeting=meeting)
    expected_view = MitupView(
        description=MeetingEditLocationMessages.DESCRIPTION.get(lang=lang),
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
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, edit_location_view(user_with_settings.meetups[0]))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION.with_id(999))], indirect=True)
async def test_edit_location_meeting_not_owned(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, handler_context=handler_context)
        # For the test case where we don´t fail but log a warning and go to main menu
        assert "User tried 'Edit location' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, handler_context=handler_context)

    assert_metrics_for_failure(1, error_type, metrics_client)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_NAME.with_id(1))], indirect=True
)
async def test_edit_location_name_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, handler_context=handler_context)
    expected_view = MitupView(
        description=MeetingEditLocationMessages.NAME_PROMPT.get(lang=user_with_settings.lang),
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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_CALLBACK, handler_context=handler_context
        )
        # If the meeting id is not found, check we have ended the conversation
        assert result is ConversationHandler.END
        assert "User tried 'Edit location name' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


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
    handler_context: HandlerContext,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, handler_context=handler_context)

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    assert_metrics_for_failure(1, error_type, metrics_client)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(1))], indirect=True
)
async def test_edit_location_coordinates_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, handler_context=handler_context
    )
    expected_view = MitupView(
        description=MeetingEditLocationMessages.COORDINATES_PROMPT.get(lang=user_with_settings.lang),
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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    # For the test case where we give a meeting that does not belong to the user
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, handler_context=handler_context
        )
        # If the meeting id is not found, check we have ended the conversation
        assert result is ConversationHandler.END
        assert "User tried 'Edit location coordinates' with a meeting that does not belong to them." in caplog.text
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


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
    handler_context: HandlerContext,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, handler_context=handler_context
        )

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    assert_metrics_for_failure(1, error_type, metrics_client)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="My Location")], indirect=True)
async def test_edit_location_name_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 1},
    )
    expected_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.NAME_SUCCESS.get(name=meeting.location.name)
    )

    assert meeting.location.name == "My Location"
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
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE, handler_context=handler_context
        )
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
async def test_edit_location_coordinates_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 1},
    )
    expected_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.COORDINATES_SUCCESS.get(lang=user_with_settings.lang)
    )

    assert meeting.location.coordinates == (123.4, 567.8)
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
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, handler_context=handler_context
        )
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.coordinates is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", [UpdateRequest(message_text="Message instead of coordinates")], indirect=True)
async def test_edit_location_coordinates_message_with_wrong_message(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 1},
    )

    expected_view = MitupView(
        description=MeetingEditLocationMessages.COORDINATES_INVALID.get(lang=user_with_settings.lang),
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
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE, handler_context=handler_context
        )
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1))], indirect=True
)
async def test_cancel_edit_meeting_location_property_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, edit_location_view(user_with_settings.meetups[0]))
    assert result is ConversationHandler.END


# ---------------------------------------------------------------------------
# LOCATION_NAME_MESSAGE — ContextPropertyNotSetError path (line 178-180)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="My Location")],
    indirect=True,
)
async def test_edit_location_name_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When no meeting_id is stored in context, edit_meeting_location_name catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
            handler_context=handler_context,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user.lang)))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_WRONG_MESSAGE — ContextPropertyNotSetError path (line 209-213)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="Message instead of coordinates")],
    indirect=True,
)
async def test_edit_location_coordinates_wrong_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When no meeting_id is stored in context, edit_coordinates_without_location catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
            handler_context=handler_context,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user.lang)))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — ContextPropertyNotSetError path (line 209-213)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When no meeting_id is stored in context, edit_meeting_location_coordinates catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
            handler_context=handler_context,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user.lang)))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — meeting is None path (line 208)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_ends_when_user_does_not_own_meeting(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """When meeting_id is set but user doesn't own that meeting, user_owns_meeting returns None,
    the handler redirects to main_menu_view and returns END."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Pass with_meeting_id=999 — user only owns meeting 1, so guard returns None.
    context, state = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 999},
    )

    assert state == ConversationHandler.END
    context.api.assert_method_just_called("edit_message", times=1)
