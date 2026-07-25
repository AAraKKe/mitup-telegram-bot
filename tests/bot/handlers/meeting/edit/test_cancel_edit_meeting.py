import logging

import pytest
from structlog.testing import capture_logs
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.models import Meetup
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import HandlerContext, UpdateRequest, call_handler, create_meetup, create_member
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(123))]), indirect=True)
async def test_cancel_edit_meeting_works(
    mock_session: MockDbSession, update: Update, meeting: Meetup, handler_context: HandlerContext
):
    mock_session.add_object(meeting)
    mock_session.add_object(meeting.owner, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.CANCEL,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123},
    )

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, meeting_views.edit_view(meeting))


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL)]), indirect=True)
async def test_cancel_edit_meeting_fails_with_malformed_callback_data(
    mock_session: MockDbSession,
    update: Update,
    meeting: Meetup,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Cancel data with no meeting id closes the flow on the main menu.

    Callback data is client-supplied, so this is a recovered interaction: it is logged as a warning
    carrying its reason, never as a fault."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.CANCEL,
            handler_context=handler_context,
            with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123},
        )

    rejections = [entry for entry in logs if entry["event"] == "Malformed callback data while cancelling meeting edit"]
    assert len(rejections) == 1
    assert rejections[0]["log_level"] == "warning"
    assert rejections[0]["reason"] == "malformed_callback_data"
    assert isinstance(rejections[0]["exc_info"], MalformedCallbackData)
    assert not [entry for entry in logs if entry["log_level"] in {"error", "critical"}]

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(123))]), indirect=True)
async def test_cancel_edit_meeting_redirects_when_meeting_no_longer_exists(
    mock_session: MockDbSession, update: Update, meeting: Meetup, handler_context: HandlerContext
):
    """A meeting that no longer resolves aborts the handler on the main menu."""
    # Register only the owner; the meeting is NOT added to the session, so the guard's
    # meeting-rooted load finds nothing.
    mock_session.add_object(meeting.owner, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.CANCEL,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123},
    )

    # The guard rejection aborts the handler, so it ends the conversation.
    assert result == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=meeting.owner.lang)))


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(999))]), indirect=True)
async def test_cancel_edit_meeting_stops_when_user_does_not_own_meeting(
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A meeting owned by somebody else aborts the handler; the error handler redirects to the main menu."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(EditMeetingHandlerId.CANCEL, handler_context=handler_context)
        assert "User tried 'Cancel edit meeting' with a meeting that does not belong to them." in caplog.text

    assert result == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
