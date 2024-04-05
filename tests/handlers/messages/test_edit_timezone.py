import logging
from typing import cast
from unittest import mock

import pytest
from telegram import Location, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import MitupContext
from mitup_bot.handlers.edit_settings.edit_timezone import (
    settings_timezone_location_message_handler,
    settings_timezone_text_message_handler,
)
from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import MockApi, UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext[mock.MagicMock],
    api: MockApi,
    get_timezone_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = cast(Message, update.effective_message).text

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != update.effective_message.text

    result = await settings_timezone_text_message_handler(update, context)

    view = factory.settings_view(
        SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=update.effective_message.text)
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == update.effective_message.text
    api.assert_send_message_called(context, update, view)
    assert result == ConversationHandler.END


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_log_with_incorrect_timezone(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext[mock.MagicMock],
    api: MockApi,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    assert update.effective_message is not None

    result = await settings_timezone_text_message_handler(update, context)

    assert (
        f"The user {user_with_settings.id} tried to set a timezone some text that is not correct. Trying again"
        in caplog.text
    )

    view = MitupView(
        description=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.CANCEL_SETTINGS,
                )
            ]
        ],
    )
    api.assert_send_message_called(context, update, view)
    assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_edit_timezone_with_location_update_correctly(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext[mock.MagicMock],
    api: MockApi,
    get_location_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = cast(Message, update.effective_message).text

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != update.effective_message.text

    result = await settings_timezone_location_message_handler(update, context)

    view = factory.settings_view(
        SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=update.effective_message.text)
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == update.effective_message.text
    api.assert_send_message_called(context, update, view)
    assert result == ConversationHandler.END


@pytest.mark.asyncio
@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_edit_timezone_with_location_log_with_incorrect_coordinates(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext[mock.MagicMock],
    api: MockApi,
    get_location_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = None

    assert update.effective_message is not None

    result = await settings_timezone_location_message_handler(update, context)

    assert (
        f"The user {user_with_settings.id} tried to set a location "
        f"{update.effective_message.location} that is not correct. "
        "Trying again" in caplog.text
    )

    view = MitupView(
        description=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.CANCEL_SETTINGS,
                )
            ]
        ],
    )
    api.assert_send_message_called(context, update, view)
    assert result == ConversationSettingsState.TIMEZONE
