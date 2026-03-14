import logging

import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.views import factory
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler, owner_with_meeting

# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_MESSAGE — ContextPropertyNotSetError path (line 156-160)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="5")],
    indirect=True,
)
async def test_edit_meeting_max_participants_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored in context, edit_meeting_max_participants catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
            update=update,
            app=app,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user.lang))


# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_WRONG_MESSAGE — ContextPropertyNotSetError path (line 186-191)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="not a number")],
    indirect=True,
)
async def test_edit_meeting_wrong_max_participants_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored in context, edit_meeting_wrong_max_participants catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
            update=update,
            app=app,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user.lang))
