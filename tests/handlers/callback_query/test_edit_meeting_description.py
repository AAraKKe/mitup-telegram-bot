import logging
import re
from unittest import mock

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.edit_meeting_description import callback_query_edit_meeting_description
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views.mitup_view import ButtonConfig, MitupView
from tests.helpers import MockApi, add_user_to_session


@pytest.mark.asyncio
async def test_callback_query_edit_meeting_title_calls_to_correct_view_and_store_meeting_id(
    mock_session: mock.MagicMock, tg_update: Update, context: MitupContext, api: MockApi, user: User
):
    assert context.user_data is not None

    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:1")
    assert match is not None

    context.matches = [match]
    add_user_to_session(mock_session, user)

    state = await callback_query_edit_meeting_description(tg_update, context)

    assert context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id == 1

    view = MitupView(
        description=MeetingMessages.EDIT_MEETING_DESCRIPTION.get(description=user.meetups[0].description),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(1),
                )
            ]
        ],
    )

    api.assert_edit_message_called(context, tg_update, view)
    assert state == ConversationMeetingState.EDIT_DESCRIPTION


@pytest.mark.asyncio
async def test_callback_query_edit_meeting_description_fails_without_callback_query_data(
    mock_session: mock.MagicMock,
    tg_update: Update,
    context: MitupContext,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_description(tg_update, context)


@pytest.mark.asyncio
async def test_edit_meeting_decription_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: mock.MagicMock,
    tg_update: Update,
    context: MitupContext,
    caplog: pytest.LogCaptureFixture,
    user: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:4")
    assert match is not None

    context.matches = [match]
    add_user_to_session(mock_session, user)

    await callback_query_edit_meeting_description(tg_update, context)

    assert "User tried editing the meeting description that does not belong to him" in caplog.text
    assert "user id: 1" in caplog.text
