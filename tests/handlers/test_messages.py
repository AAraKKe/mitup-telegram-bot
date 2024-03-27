from typing import cast
from unittest import mock

import pytest
from telegram import Message, Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handlers import ConversationSettingsState
from mitup_bot.handlers.edit_meeting.edit_meeting_description import edit_description_meeting_message_handler
from mitup_bot.handlers.edit_meeting.edit_meeting_title import edit_title_meeting_message_handler
from mitup_bot.handlers.messages import (
    ask_again_about_the_timezone,
    create_meeting_message_handler,
    filter_messages_without_text,
    registration_timezone_message_handler,
    settings_timezone_message_handler,
)
from mitup_bot.models import User
from mitup_bot.utils import MeetingMessages, SettingsMessages
from mitup_bot.views import factory
from tests.helpers import MockApi, UpdateRequest, add_meeting_to_session, add_user_to_session


@pytest.mark.asyncio
async def test_registration_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: mock.MagicMock, tg_update: Update, tg_context: mock.MagicMock, api: MockApi, user_with_settings: User
):
    add_user_to_session(mock_session, user_with_settings)

    assert user_with_settings.settings.timezone != cast(Message, tg_update.effective_message).text

    await registration_timezone_message_handler(tg_update, tg_context)

    view = factory.main_menu_view(
        SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=cast(Message, tg_update.effective_message).text)
    )

    mock_session.add.assert_called_once_with(user_with_settings)
    mock_session.flush.assert_called_once()
    assert user_with_settings.settings.timezone == cast(Message, tg_update.effective_message).text
    api.assert_send_message_called(tg_context, tg_update, view)


@pytest.mark.asyncio
async def test_settings_timezone_message_handler_set_the_correct_timezone_and_view(
    mock_session: mock.MagicMock, tg_update: Update, tg_context: mock.MagicMock, api: MockApi, user_with_settings: User
):
    add_user_to_session(mock_session, user_with_settings)

    assert tg_update.effective_message is not None
    assert user_with_settings.settings.timezone != tg_update.effective_message.text

    await settings_timezone_message_handler(tg_update, tg_context)

    view = factory.settings_view(
        SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=tg_update.effective_message.text)
    )

    mock_session.add.assert_called_once_with(user_with_settings)
    mock_session.flush.assert_called_once()
    assert user_with_settings.settings.timezone == tg_update.effective_message.text
    api.assert_send_message_called(tg_context, tg_update, view)


@pytest.mark.asyncio
async def test_filter_messages_without_text_handler_with_correct_view(
    tg_update: Update, tg_context: mock.MagicMock, api: MockApi
):
    result = await filter_messages_without_text(tg_update, tg_context)

    api.assert_send_message_called(tg_context, tg_update, factory.main_menu_view())
    assert result == -1


@pytest.mark.asyncio
async def test_ask_again_about_the_timezone_handler_with_correct_message(
    tg_update: Update, tg_context: mock.MagicMock, api: MockApi
):
    result = await ask_again_about_the_timezone(tg_update, tg_context)

    api.assert_send_message_called(tg_context, tg_update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get())
    assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
async def test_create_meeting_message_handler_creates_a_new_meeting_and_send_correct_view(
    mock_session: mock.MagicMock, tg_update: Update, tg_context: mock.MagicMock, user: User, api: MockApi
):
    add_user_to_session(mock_session, user)
    assert len(user.meetups) == 2

    def flush():
        # We flush after having added the meetup
        assert len(user.meetups) > 0, "Flush is being called without having adding a meeting first"
        user.meetups[2].id = 3

    # Mimic flush behaviour
    mock_session.flush.side_effect = flush

    await create_meeting_message_handler(tg_update, tg_context)

    assert len(user.meetups) == 3

    new_meeting = user.meetups[2]
    mock_session.add.assert_called_once_with(new_meeting)
    mock_session.flush.assert_called_once()

    message = MeetingMessages.CREATED_SUCCESS.get(title=cast(Message, tg_update.effective_message).text)
    api.assert_send_message_called(tg_context, tg_update, new_meeting.edit_view.with_context(message))


@pytest.mark.asyncio
@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_title_message_handler_update_the_title_and_send_correct_view(
    mock_session: mock.MagicMock, tg_update: Update, context: mock.MagicMock, api: MockApi, user: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    meeting = user.meetups[0]
    add_meeting_to_session(mock_session, meeting)

    assert tg_update.effective_message is not None
    assert meeting.description != tg_update.effective_message.text

    await edit_title_meeting_message_handler(tg_update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry

    mock_session.add.assert_called_once_with(meeting)
    mock_session.flush.assert_called_once()

    view = meeting.edit_view.with_context(MeetingMessages.TITLE_SET_SUCCESS.get(title=tg_update.effective_message.text))
    api.assert_send_message_called(context, tg_update, view)


@pytest.mark.asyncio
@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_description_message_handler_update_the_description_and_send_correct_view(
    mock_session: mock.MagicMock, tg_update: Update, context: MitupContext, api: MockApi, user: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, 1)

    meeting = user.meetups[0]
    add_meeting_to_session(mock_session, meeting)

    assert tg_update.effective_message is not None
    assert meeting.description != tg_update.effective_message.text

    await edit_description_meeting_message_handler(tg_update, context)

    assert ContextId.EDIT_MEETING_DESCRIPTION not in context.user_data.registry

    mock_session.add.assert_called_once_with(meeting)
    mock_session.flush.assert_called_once()

    view = meeting.edit_view.with_context(
        MeetingMessages.DESCRIPTION_SET_SUCCESS.get(description=tg_update.effective_message.text)
    )
    api.assert_send_message_called(context, tg_update, view)


@pytest.mark.asyncio
async def test_filter_messages_without_text_delete_user_data_related_with_edit_meetings(
    tg_update: Update, context: MitupContext
):
    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
    await filter_messages_without_text(tg_update, context)

    assert context.user_data.registry == {}
