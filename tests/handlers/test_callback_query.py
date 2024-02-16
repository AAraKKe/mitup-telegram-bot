from unittest import mock

import pytest

from mitup_bot.handlers.callback_query import (
    callback_query_cancel_settings,
    callback_query_main_menu,
    callback_query_settings,
    callback_query_timezone,
)
from mitup_bot.handlers.conversations_states import ConversationSettingsState
from mitup_bot.models import User
from mitup_bot.views.views import main_menu_view, settings_view


@pytest.mark.asyncio
async def test_callback_query_settings_is_called_with_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message_view") as mock_edit_message_view:
        await callback_query_settings(update, context)

        mock_edit_message_view.assert_called_once()
        assert settings_view() in mock_edit_message_view.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_timezone_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.send_message_view") as mock_send_message_view:
        with mock.patch.object(User, "find_by_tg_user_id") as mock_user_find:
            mock_user = mock.MagicMock()
            mock_user_find.return_value = mock_user
            with mock.patch(
                "mitup_bot.handlers.callback_query.change_settings_element_view"
            ) as mock_change_settings_element_view:
                result = await callback_query_timezone(update, context)

                mock_user_find.assert_called_once_with(update.effective_user.id)
                mock_send_message_view.assert_called_once()
                assert mock_change_settings_element_view.return_value in mock_send_message_view.call_args_list[0].args
                assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
@pytest.mark.parametrize("effective_user, effective_message", [(None, 1), (1, None), (None, None)])
async def test_callback_query_timezone_without_effective_user_and_message(
    effective_user, effective_message, mock_session: mock.MagicMock
):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_user = effective_user
    update.effective_message = effective_message

    with pytest.raises(RuntimeError):
        await callback_query_timezone(update, context)


@pytest.mark.asyncio
async def test_callback_query_timezone_without_found_user(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch.object(User, "find_by_tg_user_id") as mock_user_find:
        mock_user_find.return_value = None

        with pytest.raises(RuntimeError):
            await callback_query_timezone(update, context)


@pytest.mark.asyncio
async def test_callback_query_main_manu_calls_to_main_menu_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message_view") as mock_edit_message_view:
        await callback_query_main_menu(update, context)

        mock_edit_message_view.assert_called_once()
        assert main_menu_view() in mock_edit_message_view.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_cancel_setting_calls_to_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.send_message_view") as mock_send_message_view:
        await callback_query_cancel_settings(update, context)

        mock_send_message_view.assert_called_once()
        assert settings_view() in mock_send_message_view.call_args_list[0].args


@pytest.mark.asyncio
async def test_any_callback_query_fails_without_effective_chat(callback_query_list):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await callback_query_list(update, context)
