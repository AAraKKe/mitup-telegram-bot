from unittest import mock

import pytest

from mitup_bot.handlers import ConversationSettingsState
from mitup_bot.handlers.messages import (
    ask_again_about_the_timezone,
    create_meeting_message_handler,
    filter_messages_without_text,
    registration_timezone_message_handler,
    settings_timezone_message_handler,
)
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages, SettingsMessages
from mitup_bot.views.views import main_menu_view


@pytest.mark.asyncio
async def test_registration_timezone_message_handler_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Europe/Madrid"

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        with mock.patch.object(User, "by_tg_user_id") as mock_find_user:
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
        with mock.patch.object(User, "by_tg_user_id") as mock_find_user:
            mock_user = mock.MagicMock()
            mock_find_user.return_value = mock_user
            with mock.patch("mitup_bot.handlers.messages.settings_view") as mock_settings_view:
                await settings_timezone_message_handler(update, context)

                assert mock_user.settings.timezone == update.effective_message.text
                assert mock_settings_view.return_value == mock_send_message.call_args_list[0].args[2]


@pytest.mark.asyncio
async def test_filter_messages_without_text_handler_with_correct_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        result = await filter_messages_without_text(update, context)

        mock_send_message.assert_called_once_with(context, update, main_menu_view())
        assert result == -1


@pytest.mark.asyncio
async def test_ask_again_about_the_timezone_handler_with_correct_message():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
        result = await ask_again_about_the_timezone(update, context)

        mock_send_message.assert_called_once_with(
            context, update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get()
        )
        assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effective_chat, effective_user, effective_message",
    [(None, 1, 1), (1, None, 1), (1, 1, None)],
    ids=["without_chat", "without_user", "without_message"],
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
@pytest.mark.parametrize(
    "message_handler",
    [filter_messages_without_text, ask_again_about_the_timezone],
    ids=["filter_messages_without_text", "ask_again_about_the_timezone"],
)
async def test_message_handler_fails_without_effective_chat(message_handler):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await message_handler(update, context)


@pytest.mark.asyncio
async def test_create_meeting_message_handler_creates_a_new_meeting_and_send_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = "Meeting"

    with mock.patch.object(User, "by_tg_user_id") as mock_find_user:
        mock_user = mock.MagicMock()
        mock_find_user.return_value = mock_user

        meeting = Meetup(
            title=update.effective_message.text,
            owner=mock_user,
        )
        with mock.patch("mitup_bot.handlers.messages.send_message") as mock_send_message:
            await create_meeting_message_handler(update, context)

            mock_session.add.assert_called_once()
            assert isinstance(mock_session.add.call_args_list[0].args[0], Meetup)

            message = MeetingMessages.CREATED_SUCCESS.get(title=meeting.title)
            mock_send_message.assert_called_once_with(context, update, meeting.edit_view.with_context(message))


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_without_effective_text_message(
    mock_session: mock.MagicMock, message_list
):
    update = mock.AsyncMock()
    context = mock.AsyncMock()
    update.effective_message.text = None

    with pytest.raises(RuntimeError):
        await message_list(update, context)
