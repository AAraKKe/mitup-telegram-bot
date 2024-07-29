import logging

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.callback_query import CallbackQueryId
from mitup_bot.models import User
from mitup_bot.models.meetups import Meetup
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import AnyFloat, MockApi, StubMitupApp, StubMitupContext, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.callback_query") as api:
        yield api


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
    ]
    expected_metric_values = [error_count, error_count, AnyFloat()]
    expected_metric_units = [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_handler_metrics_emitted(
        expected_metric_names,
        expected_metric_values,
        units=expected_metric_units,
        exception=error_type,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(1))], indirect=True)
async def test_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(update, app, CallbackQueryId.DELETE_MEETING)

    mock_session.assert_not_deleted()
    api.assert_send_message_called(
        context,
        update,
        MitupView(
            description=MeetingMessages.DELETE_MEETING.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CONFIRM.get(),
                        callback_data=cb.CONFIRM_DELETE_MEETING.with_id(1),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DECLINE.get(),
                        callback_data=cb.DECLINE_DELETE_MEETING.with_id(1),
                    ),
                ]
            ],
        ),
    )


@pytest.mark.parametrize(
    "update, handler_id, action",
    [
        (
            (UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(999))),
            CallbackQueryId.DELETE_MEETING,
            "Delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(999))),
            CallbackQueryId.CONFIRM_DELETE_MEETING,
            "Confirm delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(999))),
            CallbackQueryId.DECLINE_DELETE_MEETING,
            "Decline delete meeting",
        ),
    ],
    ids=["delete_meeting", "confirm_delete_meeting", "decline_delete_meeting"],
    indirect=["update"],
)
async def test_delete_meeting_buttons_fails_without_existing_meeting(
    mock_session: MockDbSession,
    update: Update,
    handler_id: CallbackQueryId,
    action: str,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.guards") as _api:
        with caplog.at_level(logging.WARNING):
            context, _ = await call_handler(update, app, handler_id)

            assert f"User tried '{action}' with a meeting that does not exist." in caplog.text
            assert "Meeting id: 999, user id: 1" in caplog.text

            _api.assert_edit_message_called(
                context,
                update,
                MitupView(
                    description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(),
                    keyboard=[[ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU)]],
                ),
            )


@pytest.mark.parametrize(
    "update, handler_id, action",
    [
        (
            (UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(123))),
            CallbackQueryId.DELETE_MEETING,
            "Delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(123))),
            CallbackQueryId.CONFIRM_DELETE_MEETING,
            "Confirm delete meeting",
        ),
        (
            (UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(123))),
            CallbackQueryId.DECLINE_DELETE_MEETING,
            "Decline delete meeting",
        ),
    ],
    ids=["delete_meeting", "confirm_delete_meeting", "decline_delete_meeting"],
    indirect=["update"],
)
async def test_delete_meeting_buttons_fails_with_meeting_that_does_not_belong_to_user(
    mock_session: MockDbSession,
    update: Update,
    handler_id: CallbackQueryId,
    action: str,
    user: User,
    app: StubMitupApp,
    meeting: Meetup,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(meeting)

    with MockApi.start("mitup_bot.guards") as api:
        with caplog.at_level(logging.WARNING):
            context, _ = await call_handler(update, app, handler_id)

            assert f"User tried '{action}' with a meeting that does not belong to them." in caplog.text
            assert "Meeting id: 123, user id: 1" in caplog.text

            api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(1))], indirect=True)
async def test_confirm_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    meeting_deleted = user_with_settings.meetups[0]
    mock_session.add_object(meeting_deleted)

    context, _ = await call_handler(update, app, CallbackQueryId.CONFIRM_DELETE_MEETING)

    mock_session.assert_deleted(meeting_deleted)

    expected_view = MitupView(
        description=MeetingMessages.DELETE_MEETING_SUCCESS.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.get(),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )

    api.assert_send_message_called(context, update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(1))], indirect=True)
async def test_decline_delete_meeting_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(update, app, CallbackQueryId.DECLINE_DELETE_MEETING)

    mock_session.assert_not_deleted()
    api.assert_edit_message_called(
        context,
        update,
        user_with_settings.meetups[0].main_view.with_context(MeetingMessages.DELETE_MEETING_DECLINE.get()),
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
    app: StubMitupApp,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    context, _ = await call_handler(update, app, CallbackQueryId.DELETE_MEETING)

    assert_metrics_for_failure(1, error_type, context)


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
    app: StubMitupApp,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    context, _ = await call_handler(update, app, CallbackQueryId.CONFIRM_DELETE_MEETING)

    assert_metrics_for_failure(1, error_type, context)


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
    app: StubMitupApp,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")

    context, _ = await call_handler(update, app, CallbackQueryId.DECLINE_DELETE_MEETING)

    assert_metrics_for_failure(1, error_type, context)
