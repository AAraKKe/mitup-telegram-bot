import logging

import pytest
from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.meeting import MeetingHandlerId
from mitup_bot.models import Settings, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, HandlerContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.fixtures import create_joined_link, create_user
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
    metrics.assert_emitted(name=MetricKey.FAULT, value=error_count, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(1))], indirect=True)
async def test_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.DELETE_MEETING_CALLBACK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(1),
        ),
    )
    context.api.assert_method_just_called("send_message", times=0)


@pytest.mark.parametrize(
    "update, handler_id, action",
    [
        (
            (UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(999))),
            MeetingHandlerId.DELETE_MEETING_CALLBACK,
            "Delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(999))),
            MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
            "Confirm delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(999))),
            MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
            "Decline delete meeting",
        ),
    ],
    ids=["delete_meeting", "confirm_delete_meeting", "decline_delete_meeting"],
    indirect=["update"],
)
async def test_delete_meeting_buttons_fails_without_existing_meeting(
    mock_session: MockDbSession,
    update: Update,
    handler_id: MeetingHandlerId,
    action: str,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(handler_id, handler_context=handler_context)

        assert f"User tried '{action}' with a meeting that does not belong to them." in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text

        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "update, handler_id, action",
    [
        (
            (UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(111))),
            MeetingHandlerId.DELETE_MEETING_CALLBACK,
            "Delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(111))),
            MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
            "Confirm delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(111))),
            MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
            "Decline delete meeting",
        ),
    ],
    ids=["delete_meeting", "confirm_delete_meeting", "decline_delete_meeting"],
    indirect=["update"],
)
async def test_delete_meeting_buttons_fails_with_meeting_that_does_not_belong_to_user(
    mock_session: MockDbSession,
    update: Update,
    handler_id: MeetingHandlerId,
    action: str,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    owner = User(tg_user_id=2, first_name="Another", id=2, settings=Settings())
    meeting = create_meetup(id=111, title="Meeting", owner=owner)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(handler_id, handler_context=handler_context)

        assert f"User tried '{action}' with a meeting that does not belong to them." in caplog.text
        assert "Meeting id: 111, user id: 1" in caplog.text

        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(1))], indirect=True)
async def test_confirm_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    meeting_deleted = user_with_settings.meetups[0]
    mock_session.add_object(meeting_deleted)

    context, _ = await call_handler(MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, handler_context=handler_context)

    mock_session.assert_deleted(meeting_deleted)

    expected_view = MitupView(
        description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, expected_view)
    context.api.assert_method_just_called("send_message", times=0)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(1))], indirect=True)
async def test_decline_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(
        update,
        user_with_settings.meetups[0].main_view.with_context(
            MeetingLifecycleMessages.DELETE_DECLINED.get(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.DELETE_MEETING),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_delete_meeting_failures(
    request: pytest.FixtureRequest,
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

    context, _ = await call_handler(MeetingHandlerId.DELETE_MEETING_CALLBACK, handler_context=handler_context)

    assert_metrics_for_failure(1, error_type, metrics_client)


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.CONFIRM_DELETE_MEETING),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_confirm_delete_meeting_failures(
    request: pytest.FixtureRequest,
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

    context, _ = await call_handler(MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, handler_context=handler_context)

    assert_metrics_for_failure(1, error_type, metrics_client)


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
    failure_cases(cb.DECLINE_DELETE_MEETING),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_decline_delete_meeting_failures(
    request: pytest.FixtureRequest,
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

    context, _ = await call_handler(MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK, handler_context=handler_context)

    assert_metrics_for_failure(1, error_type, metrics_client)


# Test that when a meeting is deleted with invited users, those users are also deleted
@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(1))], indirect=True)
async def test_delete_meeting_with_invited_users_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    invited_user_1 = create_joined_link(id=1, user=create_user(id=2, tg_user_id=-1), meetup=meeting)
    invited_user_2 = create_joined_link(id=2, user=create_user(id=3, tg_user_id=-1), meetup=meeting)

    # Make the owner join as well to ensure they are not deleted
    meeting.joined_links.append(create_joined_link(user=user_with_settings, meetup=meeting))

    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, handler_context=handler_context)

    expected_delete_query = f"DELETE FROM users WHERE users.id IN ({invited_user_1.user_id}, {invited_user_2.user_id})"
    assert expected_delete_query in mock_session.queries_executed
