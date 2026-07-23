import logging
import re

import pytest
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.meeting.edit.edit_meeting_title import (
    callback_query_edit_meeting_title,
    edit_title_prompt_view,
    edit_title_rich_message_handler,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Settings, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.utils import CommonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditContentMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import rich_title
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    HandlerContext,
    MetricAssertions,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_meetup,
)
from tests.helpers.stub_db import MockDbSession

CUSTOM_EMOJI_ID = "5368324170671202286"


async def test_callback_query_edit_meeting_title_calls_to_correct_view_and_store_meeting_id(
    mock_session: MockDbSession, update: Update, context: StubMitupContext, user_with_settings: User
):
    assert context.user_data is not None

    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:1")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    state = await callback_query_edit_meeting_title(update, context)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1

    view = MitupView(
        description=MeetingEditContentMessages.TITLE_PROMPT.get(title=user_with_settings.meetups[0].title),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(1),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, view)
    assert state == ConversationMeetingState.EDIT_TITLE


async def test_callback_query_edit_meeting_title_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_title(update, context)


async def test_edit_meeting_title_does_nothing_for_meeting_not_owned_and_logs_warning(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    caplog: pytest.LogCaptureFixture,
    user_with_settings: User,
):
    caplog.set_level(logging.WARNING)

    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    owner = User(tg_user_id=2, first_name="Another", id=2, settings=Settings())
    meeting = create_meetup(id=123, title="Meeting", owner=owner)

    mock_session.add_object(meeting)

    await callback_query_edit_meeting_title(update, context)

    assert "ser tried 'Edit title' with a meeting that does not belong to them." in caplog.text
    assert " Meeting id: 123, user id: 1" in caplog.text


async def test_edit_title_rich_message_reprompts_and_keeps_state(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    assert context.user_data is not None
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting, "id")
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    state = await edit_title_rich_message_handler(update, context)

    expected = edit_title_prompt_view(meeting, user_with_settings.lang).with_context(
        CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.get(lang=user_with_settings.lang)
    )
    context.api.assert_send_message_called(update, expected)
    assert state == ConversationMeetingState.EDIT_TITLE
    # The meeting id survives so a following plain-text title still updates the meeting.
    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.RICH_MESSAGE)})


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(
            message_text="Raid night 😀",
            entities=[
                MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
                MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=11, length=2, custom_emoji_id=CUSTOM_EMOJI_ID),
            ],
        )
    ],
    indirect=True,
)
async def test_edit_title_message_stores_tagged_title_and_renders_rich_success(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TITLE_MESSAGE,
        handler_context=HandlerContext(update=update, app=app, metrics_client=metrics_client),
        with_meeting_id={ContextId.EDIT_MEETING_TITLE: meeting.db_id},
    )

    assert meeting.title == f'<b>Raid</b> night <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'
    assert meeting.plain_title == "Raid night 😀"
    assert meeting.title_tagged is None

    view = meeting_views.edit_view(meeting).with_context(
        MeetingEditContentMessages.TITLE_SUCCESS.get(title=rich_title(meeting))
    )
    context.api.assert_send_message_called(update, view)
    assert state == ConversationHandler.END
