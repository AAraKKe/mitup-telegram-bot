import logging
from typing import cast
from unittest import mock

import pytest
from telegram import Location, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_settings.edit_timezone import (
    callback_query_timezone,
    settings_timezone_location_message_handler,
    settings_timezone_text_message_handler,
)
from mitup_bot.handlers.edit_settings.entry import callback_query_settings
from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, RegistrationMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import StubMitupContext, UpdateRequest
from tests.helpers.stub_db import MockDbSession


async def test_callback_query_settings_is_called_with_settings_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_settings(update, context)

    context.api.assert_edit_message_called(update, factory.settings_view(lang=user_with_settings.lang))


async def test_callback_query_timezone_with_correct_view(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await callback_query_timezone(update, context)

    view = factory.change_settings_element_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.TIMEZONE_PROMPT.get(
            lang=user_with_settings.lang, timezone=user_with_settings.settings.timezone
        ),
    )

    context.api.assert_send_message_called(update, view)
    assert result == ConversationSettingsState.TIMEZONE


async def test_callback_query_timezone_stores_on_exit(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_timezone(update, context)

    assert context.user_data is not None
    on_exit = context.user_data.registry[ContextId.EDIT_SETTINGS_TIMEZONE].on_exit
    assert on_exit is not None
    assert on_exit.message == SettingsMessages.TIMEZONE_ON_EXIT.get(lang=user_with_settings.lang)
    assert on_exit.cancel_callback == cb.CANCEL_SETTINGS


async def test_callback_query_timezone_without_found_user(
    mock_session: MockDbSession, update: Update, context: StubMitupContext
):
    with pytest.raises(RuntimeError):
        await callback_query_timezone(update, context)


async def test_settings_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    get_timezone_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = cast(Message, update.effective_message).text

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != update.effective_message.text

    result = await settings_timezone_text_message_handler(update, context)

    view = factory.settings_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.TIMEZONE_SUCCESS.get(
            lang=user_with_settings.lang, timezone=update.effective_message.text
        ),
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == update.effective_message.text
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


async def test_settings_timezone_message_handler_log_with_incorrect_timezone(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    assert update.effective_message is not None

    result = await settings_timezone_text_message_handler(update, context)

    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    assert "User provided an invalid timezone, retrying" in caplog.text
    assert caplog.records[0].__dict__["user_id"] == user_with_settings.db_id

    view = MitupView(
        description=RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_SETTINGS,
                )
            ]
        ],
    )
    context.api.assert_send_message_called(update, view)
    assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_edit_timezone_with_location_update_correctly(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    get_location_from_api: mock.MagicMock,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = "Europe/Madrid"

    assert update.effective_message is not None
    assert user_with_settings.settings.timezone != update.effective_message.text

    result = await settings_timezone_location_message_handler(update, context)

    view = factory.settings_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.TIMEZONE_SUCCESS.get(lang=user_with_settings.lang, timezone="Europe/Madrid"),
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == "Europe/Madrid"
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", ([UpdateRequest(location=Location(123.6, 103.5))]), indirect=True)
async def test_edit_timezone_location_log_excludes_coordinates(
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

    result = await settings_timezone_location_message_handler(update, context)

    # Issue #161: the warning must not leak the user's GPS coordinates.
    assert "123.6" not in caplog.text  # latitude
    assert "103.5" not in caplog.text  # longitude
    assert "tried to set a location" not in caplog.text  # old leaking phrase
    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    assert "User provided an invalid location, retrying" in caplog.text
    assert caplog.records[0].__dict__["user_id"] == user_with_settings.db_id

    view = MitupView(
        description=RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.CANCEL_SETTINGS,
                )
            ]
        ],
    )
    context.api.assert_send_message_called(update, view)
    assert result == ConversationSettingsState.TIMEZONE
