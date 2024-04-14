import logging
import re
from typing import cast

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.edit_meeting_description import callback_query_edit_meeting_description
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.types import StubMitupApp
from mitup_bot.views.mitup_view import ButtonConfig, MitupView
from tests.helpers import MockApi, UpdateRequest, call_handler
from tests.stub_db import MockDbSession


@pytest.mark.parametrize(
    "update, expected_description",
    [
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(1)),
            "What a cool description. Congratulations",
        ),
        (UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(2)), "_This meeting has no description yet_"),
    ],
    ids=["meeting_with_a_previous_description", "meeting_without_a_previous_description"],
    indirect=["update"],
)
@pytest.mark.asyncio
async def test_callback_query_edit_meeting_description_works(
    mock_session: MockDbSession,
    update: Update,
    expected_description: str,
    user: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user, "tg_user_id")
    context, result = await call_handler(update, app, EditMeetingHandlerId.DESCRIPTION_CALLBACK)

    assert context.user_data is not None
    assert context.has_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION)

    meeting_id = context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id

    view = MitupView(
        description=MeetingMessages.EDIT_MEETING_DESCRIPTION.get(full=False, description=expected_description),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(cast(int, meeting_id)),
                )
            ]
        ],
    )

    api.assert_edit_message_called(context, update, view)
    assert result == ConversationMeetingState.EDIT_DESCRIPTION


@pytest.mark.asyncio
async def test_callback_query_edit_meeting_description_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_description(update, context)


@pytest.mark.asyncio
async def test_edit_meeting_decription_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: MitupContext,
    caplog: pytest.LogCaptureFixture,
    user: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:4")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user, "tg_user_id")

    await callback_query_edit_meeting_description(update, context)

    assert "User tried editing the meeting description that does not belong to him" in caplog.text
    assert "user id: 1" in caplog.text
