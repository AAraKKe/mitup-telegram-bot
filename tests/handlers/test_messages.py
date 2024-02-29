from unittest import mock

import pytest

from mitup_bot.handlers.messages import registration_timezone_message_handler, settings_timezone_message_handler
from mitup_bot.models import User


@pytest.mark.asyncio
async def test_registration_timezone_message_handler_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Europe/Madrid"

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        with mock.patch.object(User, "find_by_tg_user_id") as mock_find_user:
            mock_user = mock.MagicMock()
            mock_find_user.return_value = mock_user
            with mock.patch("mitup_bot.handlers.messages.main_menu_view") as mock_main_menu_view:
                await registration_timezone_message_handler(update, context)

                mock_session.add.assert_called_once_with(mock_user)
                assert mock_user.settings.timezone == update.effective_message.text
                mock_send_message.assert_called_once()
                assert mock_main_menu_view.return_value == mock_send_message.call_args_list[0].args[2]


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Europe/Madrid"

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        with mock.patch.object(User, "find_by_tg_user_id") as mock_find_user:
            mock_user = mock.MagicMock()
            mock_find_user.return_value = mock_user
            with mock.patch("mitup_bot.handlers.messages.settings_view") as mock_settings_view:
                await settings_timezone_message_handler(update, context)

                assert mock_user.settings.timezone == update.effective_message.text
                assert mock_settings_view.return_value == mock_send_message.call_args_list[0].args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effective_chat, effective_user, effective_message", [(None, 1, 1), (1, None, 1), (1, 1, None), (1, 1, None)]
)
async def test_any_message_handler_fails_without_effective_chat_user_and_message(
    mock_session: mock.MagicMock, message_list, effective_chat, effective_user, effective_message
):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = effective_chat
    update.effective_user = effective_user
    update.effective_message = effective_message

    with pytest.raises(RuntimeError):
        await message_list(update, context)


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_without_effective_text_message(
    mock_session: mock.MagicMock, message_list
):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = None

    with pytest.raises(RuntimeError):
        await message_list(update, context)
