from unittest import mock

import pytest

from mitup_bot.handlers.messages import registration_timezone_message_handler, settings_timezone_message_handler
from mitup_bot.models import User


@pytest.mark.asyncio
async def test_registration_timezone_message_handler(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Europe/Madrid"

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        with mock.patch.object(User, "find_by_tg_user_id") as mock_find_user:
            mock_user = mock.MagicMock()
            mock_find_user.return_value = mock_user

            await registration_timezone_message_handler(update, context)

            mock_user.update.assert_called_once()
            assert mock_user.settings.timezone == update.effective_message.text
            mock_send_message.assert_called_once()


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Europe/Madrid"

    with mock.patch("mitup_bot.handlers.messages.send_message_view") as mock_send_message_view:
        with mock.patch.object(User, "get_settings_from_user") as mock_get_settings:
            mock_settings = mock.MagicMock()
            mock_get_settings.return_value = mock_settings
            with mock.patch("mitup_bot.handlers.messages.settings_view") as mock_settings_view:
                await settings_timezone_message_handler(update, context)

                assert mock_settings.timezone == update.effective_message.text
                mock_settings.update.assert_called_once()
                mock_settings_view.assert_called_once()
                mock_send_message_view.assert_called_once()


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_without_effective_user(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_user = None

    with pytest.raises(RuntimeError):
        await settings_timezone_message_handler(update, context)


@pytest.mark.asyncio
async def test_any_message_handler_fails_without_effective_chat(message_list):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await message_list(update, context)
