import logging

import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.models import Meetup
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from tests.helpers import HandlerContext, UpdateRequest, call_handler, create_meetup
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
    context.api.assert_edit_message_called(update, meeting.edit_view)


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL)]), indirect=True)
async def test_cancel_edit_meeting_fails_with_malformed_callback_data(
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
    update: Update,
    meeting: Meetup,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            EditMeetingHandlerId.CANCEL,
            handler_context=handler_context,
            with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123},
        )

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(999))]), indirect=True)
async def test_cancel_edit_meeting_returns_end_when_user_does_not_own_meeting(
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """When user_owns_meeting returns None (meeting not owned), cancel_edit_meeting returns END.
    The guard itself redirects the user to the main menu via edit_message."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Register a meeting that does NOT belong to the user so user_owns_meeting returns None
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(EditMeetingHandlerId.CANCEL, handler_context=handler_context)
        assert "User tried 'Cancel edit meeting' with a meeting that does not belong to them." in caplog.text

    assert result is ConversationHandler.END
    # The guard (user_owns_meeting) calls edit_message to redirect to main menu
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
