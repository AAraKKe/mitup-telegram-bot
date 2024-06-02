import logging
import re

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.edit_meeting_title import callback_query_edit_meeting_title
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState
from mitup_bot.models.meetups import Meetup
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views.mitup_view import ButtonConfig, MitupView
from tests.helpers import MockApi
from tests.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_title") as api:
        yield api


async def test_callback_query_edit_meeting_title_calls_to_correct_view_and_store_meeting_id(
    mock_session: MockDbSession, update: Update, context: MitupContext, api: MockApi, user_with_settings: User
):
    assert context.user_data is not None

    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:1")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    state = await callback_query_edit_meeting_title(update, context)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1

    view = MitupView(
        description=MeetingMessages.EDIT_MEETING_TITLE.get(title=user_with_settings.meetups[0].title),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(1),
                )
            ]
        ],
    )

    api.assert_edit_message_called(context, update, view)
    assert state == ConversationMeetingState.EDIT_TITLE


async def test_callback_query_edit_meeting_title_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext,
):
    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_title(update, context)


async def test_edit_meeting_title_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext,
    caplog: pytest.LogCaptureFixture,
    user: User,
    meeting: Meetup,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(meeting)

    await callback_query_edit_meeting_title(update, context)

    assert "ser tried 'Edit title' with a meeting that does not belong to them." in caplog.text
    assert " Meeting id: 123, user id: 1" in caplog.text
