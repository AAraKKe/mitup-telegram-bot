import logging
import re
from collections.abc import Callable
from typing import cast

import pytest
from telegram import CallbackQuery, Update

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_meeting.edit_meeting_description import callback_query_edit_meeting_description
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views.mitup_view import ButtonConfig, MitupView
from tests.helpers import StubMitupApp, StubMitupContext, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize(
    "update, expected_description",
    [
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(1)),
            lambda lang: "What a cool description. Congratulations",
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(2)),
            lambda lang: MeetingMessages.MEETING_WITHOUT_DESCRIPTION.get(lang=lang),
        ),
    ],
    ids=["meeting_with_a_previous_description", "meeting_without_a_previous_description"],
    indirect=["update"],
)
async def test_callback_query_edit_meeting_description_works(
    mock_session: MockDbSession,
    update: Update,
    expected_description: Callable[[str], str],
    user_with_settings: User,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    callback_query = cast(CallbackQuery, update.callback_query)
    meeting_id = cast(str, callback_query.data).split(":")[1]

    mock_session.add_object(user_with_settings.meetups[int(meeting_id) - 1])

    context, result = await call_handler(EditMeetingHandlerId.DESCRIPTION_CALLBACK, update=update, app=app)

    assert context.user_data is not None
    assert context.has_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION)

    meeting_id = context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id

    view = MitupView(
        description=MeetingMessages.EDIT_MEETING_DESCRIPTION.get(
            lang=user_with_settings.lang, description=expected_description(user_with_settings.lang)
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(cast(int, meeting_id)),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, view)
    assert result == ConversationMeetingState.EDIT_DESCRIPTION


async def test_callback_query_edit_meeting_description_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_description(update, context)


async def test_edit_meeting_decription_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
    meeting: Meetup,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_edit_meeting_description(update, context)

    assert "User tried 'Edit description' with a meeting that does not exist." in caplog.text
    assert " Meeting id: 123, user id: 1" in caplog.text
