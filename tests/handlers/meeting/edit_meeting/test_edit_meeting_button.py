import logging
import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.entry import callback_query_edit_meeting
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from tests.helpers import MockApi, StubMitupContext, UpdateRequest, create_meetup
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.entry") as api:
        yield api


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_meeting_callback_fails_on_malformed_data(
    mock_session: MockDbSession, update: Update, context: StubMitupContext
):
    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:")
    assert match is not None

    assert update.effective_user is not None
    mock_session.add_object(
        User(id=1, tg_user_id=update.effective_user.id, first_name=update.effective_user.first_name),
        "tg_user_id",
    )

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting(update, context)


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_meeting_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:111")
    assert match is not None

    context.matches = [match]
    owner = User(tg_user_id=2, first_name="Another", id=2, settings=Settings())
    meeting = create_meetup(id=111, title="Meeting", owner=owner)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    await callback_query_edit_meeting(update, context)

    assert "User tried 'Edit meeting' with a meeting that does not belong to them. " in caplog.text
    assert "Meeting id: 111, user id: 1" in caplog.text


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=True)]), indirect=True)
async def test_edit_meeting_works_as_expected(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    api: MockApi,
):
    match = re.match(cb.EDIT_MEETING.pattern, "edit;meeting:1")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    view = user_with_settings.meetups[0].edit_view

    await callback_query_edit_meeting(update, context)

    api.assert_edit_message_called(context, update, view)
