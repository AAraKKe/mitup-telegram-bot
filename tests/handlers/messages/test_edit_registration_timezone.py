import logging
from typing import cast
from unittest import mock

import pytest
from telegram import Location, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.registration_process.edit_registration_timezone import (
    registration_timezone_location_message_handler,
)
from mitup_bot.handlers.registration_process.enums import (
    ConversationRegistrationProcessState,
    RegistrationProcessHandlerId,
)
from mitup_bot.models import User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import SettingsMessages
from mitup_bot.views import factory
from tests.helpers import MockApi, StubMitupApp, StubMitupContext, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", ([UpdateRequest(message_text="Something")]), indirect=True)
async def test_registration_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
    api: MockApi,
    get_timezone_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = cast(Message, update.effective_message).text

    assert user_with_settings.settings.timezone != cast(Message, update.effective_message).text

    context, result = await call_handler(update, app, RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT)

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(
        timezone=cast(Message, update.effective_message).text
    )
    view = factory.main_menu_view().with_context(message)

    mock_session.assert_added(user_with_settings)
    mock_session.assert_flushed()

    assert user_with_settings.settings.timezone == cast(Message, update.effective_message).text
    api.assert_send_message_called(context, update, view)
    assert result == ConversationHandler.END
    context.metrics_engine.assert_metrics_emited(
        names=[MetricKey.COUNT, MetricKey.ERROR],
        values=[1, 0],
        dimensions={"Feature": Feature.TIMEZONE_WITH_MESSAGE},
        add_handler_dimensions=False,
    )

    context.metrics_engine.assert_metrics_emited(
        names=[MetricKey.COUNT],
        values=[1],
        dimensions={"Feature": Feature.NEW_USER_REGISTERED},
        add_handler_dimensions=False,
    )


@pytest.mark.parametrize("update", ([UpdateRequest(message_text="some text")]), indirect=True)
async def test_registration_timezone_message_handler_log_with_incorrect_timezone(
    mock_session: MockDbSession,
    update: Update,
    api: MockApi,
    app: StubMitupApp,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    assert update.effective_message is not None

    context, result = await call_handler(update, app, RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT)

    assert (
        f"The user {user_with_settings.id} tried to set a timezone some text that is not correct. Trying again"
        in caplog.text
    )

    api.assert_send_message_called(context, update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get())
    assert result == ConversationRegistrationProcessState.TIMEZONE
    context.metrics_engine.assert_metrics_emited(
        names=[MetricKey.COUNT, MetricKey.ERROR],
        values=[1, 1],
        dimensions={"Feature": Feature.TIMEZONE_WITH_MESSAGE},
        add_handler_dimensions=False,
    )
    context.metrics_engine.assert_metrics_not_emited(
        names=[MetricKey.COUNT],
        values=[1],
        dimensions={"Feature": Feature.NEW_USER_REGISTERED},
        add_handler_dimensions=False,
    )


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_registration_timezone_with_location_update_correctly(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    api: MockApi,
    get_location_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = "Europe/Dublin"

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != "Europe/Dublin"

    result = await registration_timezone_location_message_handler(update, context)

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone="Europe/Dublin")
    view = factory.main_menu_view().with_context(message)

    mock_session.assert_added(user_with_settings)
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == "Europe/Dublin"
    api.assert_send_message_called(context, update, view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_registration_timezone_message_handler_log_with_incorrect_coordinates(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    api: MockApi,
    get_location_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = None

    assert update.effective_message is not None

    result = await registration_timezone_location_message_handler(update, context)

    assert (
        f"The user {user_with_settings.id} tried to set a location "
        f"{update.effective_message.location} that is not correct. "
        "Trying again" in caplog.text
    )

    api.assert_send_message_called(context, update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get())
    assert result == ConversationRegistrationProcessState.TIMEZONE
