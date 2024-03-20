import logging
import re
from datetime import date
from unittest import mock

import pytest
from telegram import CallbackQuery, Update
from telegram import User as TgUser

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.callback_query import (
    callback_query_cancel_meeting,
    callback_query_cancel_settings,
    callback_query_create_meeting,
    callback_query_main_menu,
    callback_query_settings,
    callback_query_show_meeting,
    callback_query_show_meetings,
    callback_query_timezone,
)
from mitup_bot.handlers.conversations_states import ConversationMeetingState, ConversationSettingsState
from mitup_bot.models import User
from mitup_bot.models.meetups import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from mitup_bot.views.views import create_meeting_view, main_menu_view, settings_view
from tests.helpers import MockApi, UpdateRequest, add_user_to_session

# Callback data from update object
CALLBACK_WITHOUT_DATA = CallbackQuery("callback_from_henry", TgUser(1, "henry", False), "chat_instance")
CALLBACK_WITH_DATA = CallbackQuery(
    "callback_from_alice", TgUser(1, "alice", False), "chat_instance", data="with_happy_data"
)

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    date=date(2001, 1, 1),
    owner=User(first_name="John", tg_user_id=1243643, username="test_username"),
)


def create_meetup(
    id: int,
    title: str = "Default title",
    description="Default description",
) -> Meetup:
    return Meetup(id=id, title=title, description=description)


@pytest.mark.asyncio
async def test_callback_query_settings_is_called_with_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.api.edit_message") as mock_edit_message:
        await callback_query_settings(update, context)

        mock_edit_message.assert_called_once()
        assert settings_view() in mock_edit_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_timezone_with_correct_view(mock_session: mock.MagicMock):
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.api.send_message") as mock_send_message:
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

    with mock.patch("mitup_bot.handlers.callback_query.api.edit_message") as mock_edit_message:
        await callback_query_list(update, context)

        mock_edit_message.assert_called_once()
        assert main_menu_view() in mock_edit_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_cancel_setting_calls_to_settings_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.api.send_message") as mock_send_message:
        await callback_query_cancel_settings(update, context)

        mock_send_message.assert_called_once()
        assert settings_view() in mock_send_message.call_args_list[0].args


@pytest.mark.asyncio
async def test_callback_query_create_meeting_calls_to_create_meeting_view():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.api.edit_message") as mock_edit_message:
        await callback_query_create_meeting(update, context)

        mock_edit_message.assert_called_once_with(context, update, create_meeting_view())


@pytest.mark.asyncio
async def test_callback_query_create_meeting_return_the_correct_state():
    update = mock.AsyncMock()
    context = mock.AsyncMock()

    with mock.patch("mitup_bot.handlers.callback_query.api.edit_message"):
        result = await callback_query_create_meeting(update, context)

        assert result == ConversationMeetingState.TITLE


@pytest.mark.asyncio
@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_callback_query_show_meeting_calls_to_meeting_view_when_meeting_is_set(
    mock_session: mock.MagicMock,
    tg_update: Update,
    tg_context: mock.MagicMock,
    user: User,
    api: MockApi,
):
    tg_context.matches = [re.match(cb.SHOW_MEETING.pattern, str(cb.SHOW_MEETING.with_id(1)))]
    add_user_to_session(mock_session, user)

    await callback_query_show_meeting(tg_update, tg_context)

    expected_view = user.meetups[0].main_view
    api.assert_edit_message_called(tg_context, tg_update, expected_view)


@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
@pytest.mark.asyncio
async def test_show_meeting_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: mock.MagicMock,
    tg_update: Update,
    tg_context: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
    user: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:4")
    assert match is not None

    tg_context.matches = [match]
    add_user_to_session(mock_session, user)

    await callback_query_show_meeting(tg_update, tg_context)

    assert "User tried opening meeting that does not belong to them" in caplog.text
    assert "Meeting id: 4, user id: 1" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, callback_data_pattern, expected",
    [
        (callback_query_show_meeting, cb.SHOW_MEETING.pattern, "show;meeting:"),
        (callback_query_show_meetings, cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:"),
    ],
    ids=["SHOW_MEETING", "SHOW_ACTIVE_MEETING_PAGE"],
)
async def test_callback_query_show_meeting_fails_without_callback_query_data(
    mock_session: mock.MagicMock,
    tg_update: Update,
    tg_context: mock.MagicMock,
    method,
    callback_data_pattern: str,
    expected: str,
):
    match = re.match(callback_data_pattern, expected)

    tg_context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await method(tg_update, tg_context)


@pytest.mark.asyncio
async def test_callback_query_show_meetings_use_correct_view(
    mock_session: mock.MagicMock, tg_update: Update, tg_context: mock.MagicMock, user: User, api: MockApi
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:1")
    tg_context.matches = [match]

    with mock.patch.object(User, "by_tg_user_id") as mock_by_tg_user_id:
        user.meetups += [create_meetup(10), create_meetup(11), create_meetup(12), create_meetup(13)]
        mock_by_tg_user_id.return_value = user
        await callback_query_show_meetings(tg_update, tg_context)

        user_meetings_buttons: list[ButtonConfig] = [
            ButtonConfig(text=str(meeting.title), callback_data=cb.SHOW_MEETING.with_id(int(meeting.id)))  # type: ignore
            for meeting in user.meetups
        ]

        expected_view = PaginatedMitupView(
            description=MeetingMessages.ACTIVE.get(),
            buttons=user_meetings_buttons,
            page_number=1,
            column_size=2,
            row_size=2,
        )
        api.assert_edit_message_called(tg_context, tg_update, expected_view)
