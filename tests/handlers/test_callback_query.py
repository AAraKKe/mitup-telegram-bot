from unittest import mock

import pytest

from mitup_bot.handlers.callback_query import (
    callback_query_cancel_settings,
    callback_query_settings,
    callback_query_timezone,
)
from mitup_bot.handlers.conversations_states import Conversation_Settings_State
from mitup_bot.models import User
from mitup_bot.views.views import settings_view


@pytest.mark.asyncio
async def test_callback_query_settings_is_called_with_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message_view") as mock_edit_message_view:
        await callback_query_settings(update, context)

        mock_edit_message_view.assert_called_once_with(context, update, settings_view())


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
                mock_change_settings_element_view.assert_called_once()
                assert result == Conversation_Settings_State.TIMEZONE


@pytest.mark.asyncio
async def test_callback_query_cancel_setting_calls_to_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.send_message_view") as mock_send_message_view:
        await callback_query_cancel_settings(update, context)

        mock_send_message_view.assert_called_once_with(context, update, settings_view())


@pytest.mark.asyncio
async def test_any_callback_query_fails_without_effective_chat(callback_query_list):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await callback_query_list(update, context)
