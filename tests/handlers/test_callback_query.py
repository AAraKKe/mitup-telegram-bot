from datetime import date
from unittest import mock

import pytest

from mitup_bot.handlers.callback_query import (
    callback_query_cancel_meeting,
    callback_query_cancel_settings,
    callback_query_create_meeting,
    callback_query_main_menu,
    callback_query_settings,
    callback_query_show_meeting,
    callback_query_timezone,
)
from mitup_bot.handlers.conversations_states import ConversationMeetingState, ConversationSettingsState
from mitup_bot.models import User
from mitup_bot.models.meetups import Meetup
from mitup_bot.views.views import create_meeting_view, main_menu_view, settings_view

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    date=date(2001, 1, 1),
    owner=User(first_name="John", tg_user_id=1243643, username="test_username"),
)


@pytest.mark.asyncio
async def test_callback_query_settings_is_called_with_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message") as mock_edit_message:
        await callback_query_settings(update, context)

        mock_edit_message.assert_called_once()
        assert settings_view() in mock_edit_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_timezone_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.send_message") as mock_send_message:
        with mock.patch.object(User, "by_tg_user_id") as mock_user_find:
            mock_user = mock.MagicMock()
            mock_user_find.return_value = mock_user
            with mock.patch(
                "mitup_bot.handlers.callback_query.change_settings_element_view"
            ) as mock_change_settings_element_view:
                result = await callback_query_timezone(update, context)

                mock_user_find.assert_called_once_with(mock_session, update.effective_user.id)
                mock_send_message.assert_called_once()
                assert mock_change_settings_element_view.return_value in mock_send_message.call_args_list[0].args
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

    with mock.patch.object(User, "by_tg_user_id") as mock_user_find:
        mock_user_find.return_value = None

        with pytest.raises(RuntimeError):
            await callback_query_timezone(update, context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_query_list",
    [callback_query_main_menu, callback_query_cancel_meeting],
    ids=["main_menu", "cancel_meeting"],
)
async def test_callback_query_show_main_menu(callback_query_list):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message") as mock_edit_message:
        await callback_query_list(update, context)

        mock_edit_message.assert_called_once()
        assert main_menu_view() in mock_edit_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_cancel_setting_calls_to_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.send_message") as mock_send_message:
        await callback_query_cancel_settings(update, context)

        mock_send_message.assert_called_once()
        assert settings_view() in mock_send_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_create_meeting_calls_to_create_meeting_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message") as mock_edit_message:
        await callback_query_create_meeting(update, context)

        mock_edit_message.assert_called_once_with(context, update, create_meeting_view())


@pytest.mark.asyncio
async def test_callback_query_create_meeting_return_the_correct_state():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.edit_message"):
        result = await callback_query_create_meeting(update, context)

        assert result == ConversationMeetingState.TITLE


@pytest.mark.asyncio
async def test_callback_query_does_not_show_meeting_without_effective_callback_query():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.callback_query = None

    with pytest.raises(RuntimeError):
        await callback_query_show_meeting(update, context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_meeting", [EXAMPLE_MEETING, None], ids=["with_found_meeting", "without_found_meeting"]
)
async def test_callback_query_show_meeting_calls_to_meeting_view_when_meeting_is_set(
    mock_session: mock.MagicMock,
    current_meeting: Meetup | None,  # type: ignore
):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.callback_query.data = "meeting_done_123"

    with mock.patch.object(User, "by_tg_user_id") as mock_by_tg_user_id:
        mock_user = mock.MagicMock()
        mock_by_tg_user_id.return_value = mock_user
        mock_user.own_meeting.return_value = current_meeting

        with mock.patch("mitup_bot.handlers.callback_query.edit_message") as mock_edit_message:
            await callback_query_show_meeting(update, context)

            if current_meeting:
                expected_view = current_meeting.main_view
                mock_edit_message.assert_called_once_with(context, update, expected_view)
            else:
                mock_edit_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_query, callback_query_data",
    [(None, mock.MagicMock), (mock.MagicMock, None)],
    ids=["without_callback_query", "without_callback_query_data"],
)
async def test_callback_query_show_meeting_fails_without_callback_query_data(callback_query, callback_query_data):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.callback_query = callback_query
    if callback_query:
        update.callback_query.data = callback_query_data

    with pytest.raises(RuntimeError):
        await callback_query_show_meeting(update, context)


@pytest.mark.asyncio
async def test_any_callback_query_fails_without_effective_chat(callback_query_list):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await callback_query_list(update, context)
