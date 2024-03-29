import logging
import re
from unittest import mock

import pytest
from telegram import Update

from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.entry import callback_query_edit_meeting
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from tests.helpers import MockApi, UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
@pytest.mark.asyncio
async def test_edit_meeting_callback_fails_on_malformed_data(
    mock_session: MockDbSession, tg_update: Update, tg_context: MitupContext[mock.MagicMock]
):
    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:")
    assert match is not None

    assert tg_update.effective_user is not None
    mock_session.add_object(
        User(id=1, tg_user_id=tg_update.effective_user.id, first_name=tg_update.effective_user.first_name),
        "tg_user_id",
    )

    tg_context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting(tg_update, tg_context)


@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
@pytest.mark.asyncio
async def test_edit_meeting_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    tg_update: Update,
    tg_context: MitupContext[mock.MagicMock],
    caplog: pytest.LogCaptureFixture,
    user: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:9")
    assert match is not None

    tg_context.matches = [match]
    mock_session.add_object(user, "tg_user_id")

    await callback_query_edit_meeting(tg_update, tg_context)

    assert "User tried editing meeting that does not belong to them" in caplog.text
    assert "Meeting id: 9, user id: 1" in caplog.text


@pytest.mark.parametrize("tg_update", ([UpdateRequest(callback_query=True)]), indirect=True)
@pytest.mark.asyncio
async def test_edit_meeting_works_as_expected(
    mock_session: MockDbSession,
    tg_update: Update,
    tg_context: MitupContext[mock.MagicMock],
    user: User,
    api: MockApi,
):
    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:1")
    assert match is not None

    tg_context.matches = [match]
    mock_session.add_object(user, "tg_user_id")
    view = user.meetups[0].edit_view

    await callback_query_edit_meeting(tg_update, tg_context)

    api.assert_edit_message_called(tg_context, tg_update, view)
