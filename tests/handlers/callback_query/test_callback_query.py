import logging
import re

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
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
from mitup_bot.utils import MeetingMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from tests.helpers import MockApi, UpdateRequest
from tests.stub_db import MockDbSession


def create_meetup(
    id: int,
    title: str = "Default title",
    description="Default description",
) -> Meetup:
    return Meetup(id=id, title=title, description=description)


@pytest.mark.asyncio
async def test_callback_query_settings_is_called_with_settings_view(
    tg_update: Update, tg_context: MitupContext, api: MockApi
):
    await callback_query_settings(tg_update, tg_context)

    api.assert_edit_message_called(tg_context, tg_update, factory.settings_view())


@pytest.mark.asyncio
async def test_callback_query_timezone_with_correct_view(
    mock_session: MockDbSession, tg_update: Update, tg_context: MitupContext, api: MockApi, user_with_settings: User
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await callback_query_timezone(tg_update, tg_context)

    view = factory.change_settings_element_view(
        SettingsMessages.SET_TIMEZONE_SETTINGS.get(timezone=user_with_settings.settings.timezone)
    )

    api.assert_send_message_called(tg_context, tg_update, view)
    assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
async def test_callback_query_timezone_without_found_user(
    mock_session: MockDbSession, tg_update: Update, tg_context: MitupContext
):
    with pytest.raises(RuntimeError):
        await callback_query_timezone(tg_update, tg_context)


@pytest.mark.asyncio
async def test_callback_query_show_main_menu(tg_update: Update, tg_context: MitupContext, api: MockApi):
    await callback_query_main_menu(tg_update, tg_context)

    api.assert_edit_message_called(tg_context, tg_update, factory.main_menu_view())


@pytest.mark.asyncio
async def test_callback_query_cancel_meeting_calls_to_corrent_view(
    tg_update: Update,
    tg_context: MitupContext,
    api: MockApi,
):
    match = re.match(cb.CANCEL_MEETING.pattern, str(cb.CANCEL_MEETING))
    assert match is not None

    tg_context.matches = [match]

    await callback_query_cancel_meeting(tg_update, tg_context)

    api.assert_edit_message_called(tg_context, tg_update, factory.main_menu_view())


@pytest.mark.asyncio
async def test_callback_query_cancel_setting_calls_to_settings_view(
    tg_update: Update, tg_context: MitupContext, api: MockApi
):
    await callback_query_cancel_settings(tg_update, tg_context)

    api.assert_send_message_called(tg_context, tg_update, factory.settings_view())


@pytest.mark.asyncio
async def test_callback_query_create_meeting_calls_to_create_meeting_view(
    tg_update: Update, tg_context: MitupContext, api: MockApi
):
    await callback_query_create_meeting(tg_update, tg_context)

    api.assert_edit_message_called(tg_context, tg_update, factory.create_meeting_view())


@pytest.mark.asyncio
async def test_callback_query_create_meeting_return_the_correct_state(
    tg_update: Update, tg_context: MitupContext, api: MockApi
):
    result = await callback_query_create_meeting(tg_update, tg_context)

    assert result == ConversationMeetingState.TITLE


@pytest.mark.asyncio
@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_callback_query_show_meeting_calls_to_meeting_view_when_meeting_is_set(
    mock_session: MockDbSession,
    tg_update: Update,
    tg_context: MitupContext,
    user: User,
    api: MockApi,
):
    match = re.match(cb.SHOW_MEETING.pattern, str(cb.SHOW_MEETING.with_id(1)))
    assert match is not None

    tg_context.matches = [match]
    mock_session.add_object(user, "tg_user_id")

    await callback_query_show_meeting(tg_update, tg_context)

    expected_view = user.meetups[0].main_view
    api.assert_edit_message_called(tg_context, tg_update, expected_view)


@pytest.mark.asyncio
async def test_show__meeting_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    tg_update: Update,
    tg_context: MitupContext,
    caplog: pytest.LogCaptureFixture,
    user: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:4")
    assert match is not None

    tg_context.matches = [match]
    mock_session.add_object(user, "tg_user_id")

    await callback_query_show_meeting(tg_update, tg_context)

    assert "User tried opening meeting that does not belong to him." in caplog.text
    assert "user id: 1" in caplog.text


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
    mock_session: MockDbSession,
    tg_update: Update,
    tg_context: MitupContext,
    method,
    callback_data_pattern: str,
    expected: str,
):
    match = re.match(callback_data_pattern, expected)
    assert match is not None

    tg_context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await method(tg_update, tg_context)


@pytest.mark.asyncio
async def test_callback_query_show_meetings_use_correct_view(
    mock_session: MockDbSession, tg_update: Update, tg_context: MitupContext, user: User, api: MockApi
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:1")
    assert match is not None

    tg_context.matches = [match]
    mock_session.add_object(user, "tg_user_id")
    user.meetups += [create_meetup(10), create_meetup(11), create_meetup(12), create_meetup(13)]

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


@pytest.mark.asyncio
async def test_callback_query_main_menu_delete_user_data_related_with_edit_meetings(
    tg_update: Update, context: MitupContext
):
    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)
    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1

    await callback_query_main_menu(tg_update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry
