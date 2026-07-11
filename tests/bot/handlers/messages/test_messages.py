from unittest import mock

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.handlers.meeting.edit.edit_meeting_description import edit_description_meeting_message_handler
from mitup_bot.handlers.meeting.edit.edit_meeting_title import edit_title_meeting_message_handler
from mitup_bot.handlers.messages import filter_messages_without_text
from mitup_bot.models import User
from mitup_bot.utils import MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import StubMitupContext, UpdateRequest
from tests.helpers.stub_db import MockDbSession


async def test_filter_messages_without_text_handler_with_correct_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await filter_messages_without_text(update, context)

    context.api.assert_send_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    assert result == -1


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_title_message_handler_update_the_title_and_send_correct_view(
    mock_session: MockDbSession, update: Update, context: mock.MagicMock, user_with_settings: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting, "id")

    assert update.effective_message is not None
    assert meeting.description != update.effective_message.text

    await edit_title_meeting_message_handler(update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry

    assert meeting.title == update.effective_message.text

    view = meeting_views.edit_view(meeting).with_context(
        MeetingEditContentMessages.TITLE_SUCCESS.get(title=update.effective_message.text)
    )
    context.api.assert_send_message_called(update, view)


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_description_message_handler_update_the_description_and_send_correct_view(
    mock_session: MockDbSession, update: Update, context: MitupContext, user_with_settings: User
):
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, 1)

    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting, "id")

    assert update.effective_message is not None
    assert meeting.description != update.effective_message.text

    await edit_description_meeting_message_handler(update, context)

    assert ContextId.EDIT_MEETING_DESCRIPTION not in context.user_data.registry

    assert meeting.description == update.effective_message.text

    view = meeting_views.edit_view(meeting).with_context(
        MeetingEditContentMessages.DESCRIPTION_SUCCESS.get(description=update.effective_message.text)
    )
    context.api.assert_send_message_called(update, view)


async def test_filter_messages_without_text_shows_main_menu_when_no_on_exit_is_set(
    update: Update, context: MitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
    await filter_messages_without_text(update, context)

    assert context.user_data.registry == {}


async def test_filter_messages_without_text_shows_on_exit_prompt_when_active_context_has_on_exit(
    update: Update, context: MitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert context.user_data is not None

    cancel_callback = cb.EDIT_MEETING_CANCEL.with_id(1)
    on_exit_message = MeetingEditContentMessages.TITLE_ON_EXIT.get(lang=user_with_settings.lang)
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)
    context.store_on_exit(ContextId.EDIT_MEETING_TITLE, on_exit_message, cancel_callback)

    result = await filter_messages_without_text(update, context)

    context.api.assert_send_message_called(
        update,
        factory.conversation_interrupted_view(
            RenderContext(lang=user_with_settings.lang),
            message=on_exit_message,
            cancel_callback=cancel_callback,
        ),
    )
    assert result is None
    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
