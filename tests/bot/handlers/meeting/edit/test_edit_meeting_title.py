import logging
import re

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.meeting.edit.edit_meeting_title import callback_query_edit_meeting_title
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditContentMessages
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import StubMitupContext, create_meetup
from tests.helpers.stub_db import MockDbSession


async def test_callback_query_edit_meeting_title_calls_to_correct_view_and_store_meeting_id(
    mock_session: MockDbSession, update: Update, context: StubMitupContext, user_with_settings: User
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
        description=MeetingEditContentMessages.TITLE_PROMPT.get(title=user_with_settings.meetups[0].title),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, view)
    assert state == ConversationMeetingState.EDIT_TITLE


async def test_callback_query_edit_meeting_title_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_title(update, context)


async def test_edit_meeting_title_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    owner = User(tg_user_id=2, first_name="Another", id=2, settings=Settings())
    meeting = create_meetup(id=123, title="Meeting", owner=owner)

    mock_session.add_object(meeting)

    await callback_query_edit_meeting_title(update, context)

    assert "ser tried 'Edit title' with a meeting that does not belong to them." in caplog.text
    assert " Meeting id: 123, user id: 1" in caplog.text
