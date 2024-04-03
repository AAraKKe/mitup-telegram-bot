from typing import cast
from unittest import mock

import pytest
from telegram import Message, Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handlers.edit_meeting.edit_meeting_description import edit_description_meeting_message_handler
from mitup_bot.handlers.edit_meeting.edit_meeting_title import edit_title_meeting_message_handler
from mitup_bot.handlers.messages import create_meeting_message_handler, filter_messages_without_text
from mitup_bot.models import User
from mitup_bot.utils import MeetingMessages
from mitup_bot.views import factory
from tests.helpers import MockApi, UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.asyncio
async def test_filter_messages_without_text_handler_with_correct_view(
    update: Update, context: MitupContext[mock.MagicMock], api: MockApi
):
    result = await filter_messages_without_text(update, context)

    api.assert_send_message_called(context, update, factory.main_menu_view())
    assert result == -1


@pytest.mark.asyncio
async def test_create_meeting_message_handler_creates_a_new_meeting_and_send_correct_view(
    mock_session: MockDbSession, update: Update, context: MitupContext[mock.MagicMock], user: User, api: MockApi
):
    mock_session.add_object(user, "tg_user_id")
    assert len(user.meetups) == 2

    def flush():
        # We flush after having added the meetup
        assert len(user.meetups) > 0, "Flush is being called without having adding a meeting first"
        user.meetups[2].id = 3

    # Mimic flush behaviour
    mock_session.flush.side_effect = flush

    await create_meeting_message_handler(update, context)

    assert len(user.meetups) == 3

    new_meeting = user.meetups[2]
    mock_session.assert_added(new_meeting)
    mock_session.assert_flushed()

    message = MeetingMessages.CREATED_SUCCESS.get(title=cast(Message, update.effective_message).text)
    api.assert_send_message_called(context, update, new_meeting.edit_view.with_context(message))


@pytest.mark.asyncio
@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_title_message_handler_update_the_title_and_send_correct_view(
    mock_session: MockDbSession, update: Update, context: mock.MagicMock, api: MockApi, user: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    meeting = user.meetups[0]
    mock_session.add_object(meeting, "id")

    assert update.effective_message is not None
    assert meeting.description != update.effective_message.text

    await edit_title_meeting_message_handler(update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry

    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    view = meeting.edit_view.with_context(MeetingMessages.TITLE_SET_SUCCESS.get(title=update.effective_message.text))
    api.assert_send_message_called(context, update, view)


@pytest.mark.asyncio
@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_description_message_handler_update_the_description_and_send_correct_view(
    mock_session: MockDbSession, update: Update, context: MitupContext, api: MockApi, user: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, 1)

    meeting = user.meetups[0]
    mock_session.add_object(meeting, "id")

    assert update.effective_message is not None
    assert meeting.description != update.effective_message.text

    await edit_description_meeting_message_handler(update, context)

    assert ContextId.EDIT_MEETING_DESCRIPTION not in context.user_data.registry

    mock_session.assert_added(meeting)
    mock_session.assert_flushed()

    view = meeting.edit_view.with_context(
        MeetingMessages.DESCRIPTION_SET_SUCCESS.get(description=update.effective_message.text)
    )
    api.assert_send_message_called(context, update, view)


@pytest.mark.asyncio
async def test_filter_messages_without_text_delete_user_data_related_with_edit_meetings(
    update: Update, context: MitupContext
):
    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
    await filter_messages_without_text(update, context)

    assert context.user_data.registry == {}
