from unittest import mock

import pytest
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.registration_process.edit_registration_timezone import (
    registration_timezone_invalid_input_handler,
    registration_timezone_location_message_handler,
    registration_timezone_text_message_handler,
)
from mitup_bot.handlers.registration_process.enums import ConversationRegistrationProcessState
from mitup_bot.models import User
from mitup_bot.utils import SettingsMessages
from mitup_bot.views import factory
from tests.helpers import StubMitupContext, UpdateRequest
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def get_timezone_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


@pytest.fixture
def get_location_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_location") as location_patch:
        yield location_patch


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(command="start"),
        UpdateRequest(location=Location(latitude=1.0, longitude=1.0)),
    ],
    indirect=True,
    ids=["command", "location"],
)
async def test_registration_timezone_invalid_input_handler_sends_invalid_input_message_and_stays_in_timezone_state(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await registration_timezone_invalid_input_handler(update, context)

    context.api.assert_send_message_called(
        update,
        SettingsMessages.REGISTRATION_TIMEZONE_INVALID_INPUT.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE


async def test_registration_timezone_text_message_handler_sets_timezone_and_ends_conversation(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert update.effective_message is not None
    get_timezone_from_api.return_value = update.effective_message.text

    result = await registration_timezone_text_message_handler(update, context)

    view = factory.main_menu_view(lang=user_with_settings.lang).with_context(
        SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=update.effective_message.text)
    )
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == update.effective_message.text
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


async def test_registration_timezone_text_message_handler_stays_in_timezone_state_when_address_not_recognized(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    result = await registration_timezone_text_message_handler(update, context)

    context.api.assert_send_message_called(
        update,
        SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_registration_timezone_location_message_handler_sets_timezone_and_ends_conversation(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = "Europe/Madrid"

    result = await registration_timezone_location_message_handler(update, context)

    view = factory.main_menu_view(lang=user_with_settings.lang).with_context(
        SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone="Europe/Madrid")
    )
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == "Europe/Madrid"
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_registration_timezone_location_message_handler_stays_in_timezone_state_when_location_not_recognized(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = None

    result = await registration_timezone_location_message_handler(update, context)

    context.api.assert_send_message_called(
        update,
        SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE
