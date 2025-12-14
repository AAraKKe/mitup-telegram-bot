import logging

import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.models import Meetup
from mitup_bot.models.users import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from tests.helpers import StubMitupApp, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(123))]), indirect=True)
async def test_cancel_edit_meeting_works(
    mock_session: MockDbSession, update: Update, meeting: Meetup, app: StubMitupApp
):
    mock_session.add_object(meeting)
    mock_session.add_object(meeting.owner, "tg_user_id")

    context, result = await call_handler(
        update, app, EditMeetingHandlerId.CANCEL, with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123}
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
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(
            update, app, EditMeetingHandlerId.CANCEL, with_meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: 123}
        )

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)
    assert result is ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))
