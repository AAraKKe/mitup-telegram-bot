import re
from collections.abc import Callable
from typing import cast

import pytest
from telegram import CallbackQuery, MessageEntity, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, MeetingGoneError
from mitup_bot.handlers.meeting.edit.edit_meeting_description import (
    callback_query_edit_meeting_description,
    edit_description_prompt_view,
    edit_description_rich_message_handler,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.utils import CommonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingDisplayMessages, MeetingEditContentMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import rich_description
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    HandlerContext,
    MetricAssertions,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
)
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
            lambda lang: MeetingDisplayMessages.DESCRIPTION_EMPTY.get(lang=lang),
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
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    callback_query = cast(CallbackQuery, update.callback_query)
    meeting_id = cast(str, callback_query.data).split(":")[1]

    mock_session.add_object(user_with_settings.meetups[int(meeting_id) - 1])

    context, result = await call_handler(EditMeetingHandlerId.DESCRIPTION_CALLBACK, handler_context=handler_context)

    assert context.user_data is not None
    assert context.has_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION)

    meeting_id = context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id

    view = MitupView(
        description=MeetingEditContentMessages.DESCRIPTION_PROMPT.get(
            lang=user_with_settings.lang, description=expected_description(user_with_settings.lang)
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=user_with_settings.lang),
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


async def test_edit_meeting_description_stops_for_meeting_that_is_gone(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    meeting: Meetup,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")

    with pytest.raises(MeetingGoneError) as raised:
        await callback_query_edit_meeting_description(update, context)

    assert "User tried 'Edit description' with a meeting that does not exist." in str(raised.value)
    assert " Meeting id: 123, user id: 1" in str(raised.value)
    context.api.assert_edit_message_not_called()


async def test_edit_description_rich_message_reprompts_and_keeps_state(
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
    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, 1)

    state = await edit_description_rich_message_handler(update, context)

    expected = edit_description_prompt_view(meeting, user_with_settings.lang).with_context(
        CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.get(lang=user_with_settings.lang)
    )
    context.api.assert_send_message_called(update, expected)
    assert state == ConversationMeetingState.EDIT_DESCRIPTION
    assert context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id == 1
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.RICH_MESSAGE)})


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(
            message_text="Bring snacks & drinks",
            entities=[MessageEntity(type=MessageEntity.ITALIC, offset=6, length=6)],
        )
    ],
    indirect=True,
)
async def test_edit_description_message_stores_tagged_description_and_renders_rich_success(
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
        EditMeetingHandlerId.DESCRIPTION_MESSAGE,
        handler_context=HandlerContext(update=update, app=app, metrics_client=metrics_client),
        with_meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: meeting.db_id},
    )

    assert meeting.description == "Bring <i>snacks</i> &amp; drinks"
    assert meeting.plain_description == "Bring snacks & drinks"

    view = meeting_views.edit_view(meeting).with_context(
        MeetingEditContentMessages.DESCRIPTION_SUCCESS.get(description=rich_description(meeting))
    )
    context.api.assert_send_message_called(update, view)
    assert state == ConversationHandler.END
