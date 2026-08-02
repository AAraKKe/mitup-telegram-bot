import logging

import pytest
from structlog.testing import capture_logs
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.meeting.edit.edit_meeting_location import (
    edit_location_name_prompt_view,
    edit_location_name_rich_message_handler,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.views import edit_location_view
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, MeetupLocation, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingEditLocationMessages
from mitup_bot.views import MitupView, RenderContext, factory
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


def test_edit_location_view(meeting: Meetup, lang: str):
    meeting_id = meeting.db_id
    meeting.language = lang

    result = edit_location_view(meeting=meeting)
    expected_view = MitupView(
        description=MeetingEditLocationMessages.DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_NAME.get_text(lang=lang),
                    callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_COORDINATES.get_text(lang=lang),
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
        description=MeetingEditLocationMessages.NAME_PROMPT.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
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
        description=MeetingEditLocationMessages.COORDINATES_PROMPT.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
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
            message=CommonMessages.CONTEXT_LOST.get(lang=user_with_settings.lang),
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
            message=CommonMessages.CONTEXT_LOST.get(lang=user_with_settings.lang),
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
        description=MeetingEditLocationMessages.COORDINATES_INVALID.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
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
            message=CommonMessages.CONTEXT_LOST.get(lang=user_with_settings.lang),
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

    context.api.assert_edit_message_called(update, edit_location_view(user_with_settings.meetups[0]))
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
        factory.main_menu_view(RenderContext(lang=user.lang), message=CommonMessages.CONTEXT_LOST.get(lang=user.lang)),
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
        factory.main_menu_view(RenderContext(lang=user.lang), message=CommonMessages.CONTEXT_LOST.get(lang=user.lang)),
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
        factory.main_menu_view(RenderContext(lang=user.lang), message=CommonMessages.CONTEXT_LOST.get(lang=user.lang)),
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
    expected_view = edit_location_view(session_meeting).with_context(
        MeetingEditLocationMessages.COORDINATES_SUCCESS.get(lang=user.lang)
    )
    context.api.assert_send_message_called(update, expected_view)
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
        CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.get(lang=user_with_settings.lang)
    )
    context.api.assert_send_message_called(update, expected)
    assert state == ConversationMeetingState.EDIT_LOCATION_NAME
    assert context.user_data.registry[ContextId.EDIT_MEETING_LOCATION_NAME].meeting_id == 1
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.RICH_MESSAGE)})
