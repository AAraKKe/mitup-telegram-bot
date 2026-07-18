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
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.utils import RegistrationMessages
from mitup_bot.views import RenderContext, factory
from tests.helpers import StubMitupContext, UpdateRequest, call_handler, claimed_state
from tests.helpers.handler_context import HandlerContext
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", ([UpdateRequest(message_text="Something")]), indirect=True)
async def test_registration_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    get_timezone_from_api: mock.MagicMock,
    user_with_settings: User,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = cast(Message, update.effective_message).text

    assert user_with_settings.settings.timezone != cast(Message, update.effective_message).text

    context, result = await call_handler(
        RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, handler_context=handler_context
    )

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(
        timezone=cast(Message, update.effective_message).text, lang=user_with_settings.lang
    )
    view = factory.main_menu_view(RenderContext(lang=user_with_settings.lang)).with_context(message)

    mock_session.assert_added(user_with_settings)
    mock_session.assert_flushed()

    assert user_with_settings.settings.timezone == cast(Message, update.effective_message).text
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END
    metrics.assert_emitted(
        name=MetricKey.COUNT,
        value=1,
        dimensions={"Feature": str(Feature.SET_TIMEZONE)},
        properties={"InputMethod": "message"},
    )
    metrics.assert_emitted(
        name=MetricKey.ERROR,
        value=0,
        dimensions={"Feature": str(Feature.SET_TIMEZONE)},
        properties={"InputMethod": "message"},
    )
    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.NEW_USER_REGISTERED)})


@pytest.mark.parametrize("update", ([UpdateRequest(message_text="some text")]), indirect=True)
async def test_registration_timezone_message_handler_log_with_incorrect_timezone(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    assert update.effective_message is not None

    context, result = await call_handler(
        RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, handler_context=handler_context
    )

    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    assert "User provided an invalid timezone, retrying" in caplog.text
    assert caplog.records[0].__dict__["user_id"] == user_with_settings.db_id

    context.api.assert_send_message_called(update, RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang))
    assert result == ConversationRegistrationProcessState.TIMEZONE
    metrics.assert_emitted(
        name=MetricKey.COUNT,
        value=1,
        dimensions={"Feature": str(Feature.SET_TIMEZONE)},
        properties={"InputMethod": "message"},
    )
    metrics.assert_emitted(
        name=MetricKey.ERROR,
        value=1,
        dimensions={"Feature": str(Feature.SET_TIMEZONE)},
        properties={"InputMethod": "message"},
    )
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.NEW_USER_REGISTERED)})


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_registration_timezone_with_location_update_correctly(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    get_location_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = "Europe/Dublin"

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != "Europe/Dublin"

    result = await claimed_state(registration_timezone_location_message_handler(update, context))

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(timezone="Europe/Dublin", lang=user_with_settings.lang)
    view = factory.main_menu_view(RenderContext(lang=user_with_settings.lang)).with_context(message)

    mock_session.assert_added(user_with_settings)
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == "Europe/Dublin"
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_registration_timezone_message_handler_log_excludes_coordinates(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    get_location_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = None

    assert update.effective_message is not None

    result = await claimed_state(registration_timezone_location_message_handler(update, context))

    # Issue #161: the warning must not leak the user's GPS coordinates.
    assert "123.6" not in caplog.text  # latitude
    assert "103.5" not in caplog.text  # longitude
    assert "tried to set a location" not in caplog.text  # old leaking phrase
    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    assert "User provided an invalid location, retrying" in caplog.text
    assert caplog.records[0].__dict__["user_id"] == user_with_settings.db_id

    context.api.assert_send_message_called(update, RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang))
    assert result == ConversationRegistrationProcessState.TIMEZONE
