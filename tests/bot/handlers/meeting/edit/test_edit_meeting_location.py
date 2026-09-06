import logging

import pytest
from structlog.testing import capture_logs
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot import limits
from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.meeting.edit.edit_meeting_location import (
    edit_location_name_prompt_view,
    edit_location_name_rich_message_handler,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import MeetingCounts, MeetupLocation, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingEditLocationMessages
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    StubMitupContext,
    UpdateRequest,
    assert_context_lost_logged,
    assert_meeting_rejection_logged,
    call_handler,
    create_meetup,
    create_member,
    log_record,
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


def assert_metrics_for_failure(error_type: type[Exception], metrics_client: MetricsClient):
    """Assert the invocation closed with exactly one `Fault` sample, naming its class when it faulted.

    A caller with no account row is an expected business state the error handler answers, so its
    sample is a 0 carrying no `error_type`; every other failure reaching here is a genuine fault.
    """
    handled = issubclass(error_type, UserNotFound)
    metrics = MetricAssertions(metrics_client)
    metrics.assert_emitted(
        name=MetricKey.FAULT, value=0 if handled else 1, times=1, exception=None if handled else error_type
    )
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


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

    context.api.assert_edit_message_called(
        update,
        meeting_views.owner_view(user_with_settings.meetups[0]).with_context(
            CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user_with_settings.lang)
        ),
    )


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
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, handler_context=handler_context)
        # For the test case where we don´t fail but log a warning and go to main menu
        assert_meeting_rejection_logged(caplog, action="Edit location", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
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
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.LOCATION_CALLBACK, handler_context=handler_context)

    assert_metrics_for_failure(error_type, metrics_client)


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
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, handler_context=handler_context)
    expected_view = MitupView(
        message=MeetingEditLocationMessages.NAME_PROMPT.rich(lang=user_with_settings.lang),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, expected_view)
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
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_CALLBACK, handler_context=handler_context
        )
        # The guard rejection aborts the handler, so it ends the conversation.
        assert result == ConversationHandler.END
        assert_meeting_rejection_logged(caplog, action="Edit location name", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
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

    assert_metrics_for_failure(error_type, metrics_client)


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
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, handler_context=handler_context
    )
    expected_view = MitupView(
        message=MeetingEditLocationMessages.COORDINATES_PROMPT.rich(lang=user_with_settings.lang),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, expected_view)
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
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, handler_context=handler_context
        )
        # The guard rejection aborts the handler, so it ends the conversation.
        assert result == ConversationHandler.END
        assert_meeting_rejection_logged(caplog, action="Edit location coordinates", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    # Check that meeting id has not been set
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
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

    assert_metrics_for_failure(error_type, metrics_client)


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
    assert meeting.location.name == "My Location"
    context.api.assert_send_message_called(update, meeting_views.owner_view(meeting))
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="My Location")], indirect=True)
async def test_edit_location_name_message_fails_if_context_not_saved(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE, handler_context=handler_context
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_NAME)

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user_with_settings.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


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
    assert meeting.location.coordinates == (123.4, 567.8)
    context.api.assert_send_message_called(update, meeting_views.owner_view(meeting))
    assert result is ConversationHandler.END
    # Meeting id has been cleaned from the context
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES)


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
async def test_edit_location_coordinates_message_fails_if_context_not_saved(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, handler_context=handler_context
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    assert meeting.location.coordinates is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user_with_settings.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


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
        message=MeetingEditLocationMessages.COORDINATES_INVALID.rich(lang=user_with_settings.lang),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=user_with_settings.lang),
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
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE, handler_context=handler_context
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    assert result is ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user_with_settings.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


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

    context.api.assert_edit_message_called(update, meeting_views.owner_view(user_with_settings.meetups[0]))
    assert result is ConversationHandler.END


# ---------------------------------------------------------------------------
# LOCATION_NAME_MESSAGE — ContextPropertyNotSetError path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="My Location")],
    indirect=True,
)
async def test_edit_location_name_message_sends_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When no meeting_id is stored in context, edit_meeting_location_name catches
    ContextPropertyNotSetError, sends the main menu view as a new message, and ends the conversation.

    The loss is an expected state, so it is logged as a warning and counted on the ContextLost
    series the central error handler feeds — not on the fault series."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with capture_logs() as logs:
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
            handler_context=handler_context,
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_NAME)
    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_WRONG_MESSAGE — ContextPropertyNotSetError path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="Message instead of coordinates")],
    indirect=True,
)
async def test_edit_location_coordinates_wrong_message_sends_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When no meeting_id is stored in context, edit_coordinates_without_location catches
    ContextPropertyNotSetError, sends the main menu view as a new message, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with capture_logs() as logs:
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
            handler_context=handler_context,
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_COORDINATES)
    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — ContextPropertyNotSetError path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_sends_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When no meeting_id is stored in context, edit_meeting_location_coordinates catches
    ContextPropertyNotSetError, sends the main menu view as a new message, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with capture_logs() as logs:
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
            handler_context=handler_context,
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_LOCATION_COORDINATES)
    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — meeting is None path (line 208)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_stops_when_user_does_not_own_meeting(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """A meeting the user does not own redirects to the main menu and aborts the handler."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Pass with_meeting_id=999 — user only owns meeting 1, so the guard rejects the caller.
    context, state = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 999},
    )

    assert state == ConversationHandler.END
    # A message update carries no message of ours to replace, so the redirect is a fresh reply.
    context.api.assert_method_just_called("send_message", times=1)
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=123.4, latitude=567.8))], indirect=True)
async def test_edit_location_coordinates_message_mutates_session_meeting(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
):
    """The coordinate write and fan-out land on the guard's meeting-rooted load, whose participant
    leaves are hydrated — a user-rooted instance leaves them unloaded under the async engine."""
    user, user_rooted_meeting = owner_with_meeting(meeting_id=1)
    # A distinct Meetup instance shares the same id: the guard resolves this session-loaded one,
    # and it is the only one that may be mutated.
    session_meeting = create_meetup(id=1, owner=user, location=MeetupLocation(name="Session Location"))
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(session_meeting)

    context, result = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 1},
    )

    assert session_meeting.location.coordinates == (123.4, 567.8)
    assert user_rooted_meeting.location.coordinates is None
    context.api.assert_send_message_called(update, meeting_views.owner_view(session_meeting))
    assert result is ConversationHandler.END


async def test_edit_location_name_rich_message_reprompts_and_keeps_state(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    assert context.user_data is not None
    mock_session.add_object(user_with_settings, "tg_user_id")
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 1)

    state = await edit_location_name_rich_message_handler(update, context)

    expected = edit_location_name_prompt_view(1, user_with_settings.lang).with_context(
        CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.rich(lang=user_with_settings.lang)
    )
    context.api.assert_send_message_called(update, expected)
    assert state == ConversationMeetingState.EDIT_LOCATION_NAME
    assert context.user_data.registry[ContextId.EDIT_MEETING_LOCATION_NAME].meeting_id == 1
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.RICH_MESSAGE)})


# ---------------------------------------------------------------------------
# LOCATION_NAME_MESSAGE — the place-name character cap
# ---------------------------------------------------------------------------


OVER_CAP_LOCATION_NAME = "l" * (limits.LOCATION_NAME_MAX_CHARS + 1)


@pytest.mark.parametrize("update", [UpdateRequest(message_text=OVER_CAP_LOCATION_NAME)], indirect=True)
async def test_over_cap_location_name_leaves_the_venue_untouched_and_reprompts(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """A place name past the cap never reaches the meeting, and the prompt comes back with the
    error on top so the Cancel button stays reachable."""
    caplog.set_level(logging.INFO)
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 1},
    )

    assert meeting.location.name is None
    assert state == ConversationMeetingState.EDIT_LOCATION_NAME
    # The meeting id survives the refusal, so a shorter retry edits this same meeting.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)
    context.api.assert_method_just_called("update_meeting_messages", times=0)

    error = MeetingEditLocationMessages.LOCATION_NAME_TOO_LONG.rich(
        lang=user_with_settings.lang, length=len(OVER_CAP_LOCATION_NAME), limit=limits.LOCATION_NAME_MAX_CHARS
    )
    assert str(len(OVER_CAP_LOCATION_NAME)) in error.text
    assert str(limits.LOCATION_NAME_MAX_CHARS) in error.text
    assert "${" not in error.text
    context.api.assert_send_message_called(
        update, edit_location_name_prompt_view(1, user_with_settings.lang, error=error)
    )

    # The shared line is pinned in full by the title test; this only proves it names this field.
    record = log_record(caplog, "Meeting edit input rejected")
    assert record.__dict__["field"] == "location_name"
    assert record.__dict__["reason"] == "too_long"


@pytest.mark.parametrize("update", [UpdateRequest(message_text="l" * limits.LOCATION_NAME_MAX_CHARS)], indirect=True)
async def test_location_name_at_the_cap_is_stored(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """The cap is inclusive: a place name of exactly the maximum length is a normal edit."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    _, state = await call_handler(
        EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 1},
    )

    assert meeting.location.name == "l" * limits.LOCATION_NAME_MAX_CHARS
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# Remove location name: confirmation, confirm, decline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DELETE_MEETING_LOCATION_NAME.with_id(1))],
    indirect=True,
)
async def test_remove_location_name_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(name="The Old Cafe")
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.REMOVE_LOCATION_NAME_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditLocationMessages.REMOVE_NAME_CONFIRMATION.rich(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_LOCATION_NAME.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_LOCATION_NAME.with_id(1),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_LOCATION_NAME.with_id(1))],
    indirect=True,
)
async def test_confirm_remove_location_name_clears_it_and_keeps_the_pin(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(name="The Old Cafe", coordinates=(123.4, 567.8))
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_LOCATION_NAME_CALLBACK, handler_context=handler_context
    )

    assert meeting.location.name is None
    assert meeting.location.coordinates == (123.4, 567.8)

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_LOCATION_NAME.with_id(1))],
    indirect=True,
)
async def test_decline_remove_location_name_keeps_it(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(name="The Old Cafe")
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_LOCATION_NAME_CALLBACK, handler_context=handler_context
    )

    assert meeting.location.name == "The Old Cafe"
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))


# ---------------------------------------------------------------------------
# Remove coordinates: confirmation, confirm, decline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DELETE_MEETING_COORDINATES.with_id(1))],
    indirect=True,
)
async def test_remove_coordinates_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(coordinates=(123.4, 567.8))
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.REMOVE_COORDINATES_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditLocationMessages.REMOVE_COORDINATES_CONFIRMATION.rich(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_COORDINATES.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_COORDINATES.with_id(1),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_COORDINATES.with_id(1))],
    indirect=True,
)
async def test_confirm_remove_coordinates_clears_them_and_keeps_the_name(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(name="The Old Cafe", coordinates=(123.4, 567.8))
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_COORDINATES_CALLBACK, handler_context=handler_context
    )

    assert meeting.location.coordinates is None
    assert meeting.location.name == "The Old Cafe"

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_COORDINATES.with_id(1))],
    indirect=True,
)
async def test_decline_remove_coordinates_keeps_them(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.location = MeetupLocation(coordinates=(123.4, 567.8))
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_COORDINATES_CALLBACK, handler_context=handler_context
    )

    assert meeting.location.coordinates == (123.4, 567.8)
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
