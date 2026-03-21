import logging
import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.handlers.meeting.show_meeting import callback_query_show_meeting
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_meetup,
)


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(1))]), indirect=True)
async def test_show_meeting_calls_to_meeting_view_when_meeting_is_set(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Ensure meetups[0] is ID 1 and owned by user_with_settings for the test's UpdateRequest
    # The user_with_settings fixture should ideally set this up.
    # If not, we need to adjust it here or the fixture.
    # Assuming user_with_settings.meetups is populated by the fixture and meetups[0] has id=1.
    if not user_with_settings.meetups or user_with_settings.meetups[0].db_id != 1:
        # If fixture doesn't provide meetups[0] as id=1, create/modify it
        # This is a fallback, ideally fixture is canonical
        meeting1 = create_meetup(id=1, title="Test Meeting 1 for Show", owner=user_with_settings)
        if user_with_settings.meetups:
            # Try to replace or ensure it's the first for consistency if test relies on meetups[0]
            if user_with_settings.meetups[0].db_id == 1:
                user_with_settings.meetups[0] = meeting1  # replace if id matches but owner might be wrong
            else:
                user_with_settings.meetups.insert(0, meeting1)  # add at beginning
        else:
            user_with_settings.meetups = [meeting1]
    elif user_with_settings.meetups[0].owner != user_with_settings:
        user_with_settings.meetups[0].owner = user_with_settings

    target_meeting = user_with_settings.meetups[0]
    assert target_meeting.db_id == 1, "Target meeting for test should have ID 1"
    assert target_meeting.owner == user_with_settings, "Target meeting not owned by user_with_settings"

    mock_session.add_object(target_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    expected_view = target_meeting.main_view
    context.api.assert_edit_message_called(update, expected_view)


async def test_show_meeting_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:4")  # Target meeting ID 4
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Create another user to be the owner of meeting 4
    # Ensure this other_user has a different DB id from user_with_settings
    other_user_db_id = (user_with_settings.id + 1) if user_with_settings.id is not None else 999
    other_user_tg_id = (user_with_settings.tg_user_id + 1) if user_with_settings.tg_user_id is not None else 9990
    other_user = User(id=other_user_db_id, tg_user_id=other_user_tg_id, first_name="Other Owner")
    mock_session.add_object(other_user, "tg_user_id")

    # Create meeting 4, owned by other_user
    other_meeting = create_meetup(id=4, owner=other_user, title="Other's Meeting")
    mock_session.add_object(other_meeting)

    await callback_query_show_meeting(update, context)

    # Ensure that the only message sent was the edit for the main menu to avoid information flow
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang), times=1)
    context.api.assert_send_message_not_called()
    assert (
        "User tried 'Show meeting' with a meeting that does not belong to them. "
        f"Meeting id: 4, user id: {user_with_settings.id}"
    ) in caplog.text


async def test_show_meeting_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_meeting(update, context)
