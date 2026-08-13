import logging
import re

import pytest
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler

from mitup_bot import limits
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, MeetingNotOwnedError
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
    log_record,
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
        description=MeetingEditContentMessages.TITLE_PROMPT.get(
            lang=user_with_settings.lang, title=user_with_settings.meetups[0].title
        ),
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


async def test_edit_meeting_title_stops_for_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
):
    match = re.match(cb.EDIT_MEETING_TITLE.pattern, "edit;meet_title:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    owner = User(tg_user_id=2, first_name="Another", id=2, settings=Settings())
    meeting = create_meetup(id=123, title="Meeting", owner=owner)

    mock_session.add_object(meeting)

    with pytest.raises(MeetingNotOwnedError) as raised:
        await callback_query_edit_meeting_title(update, context)

    assert "ser tried 'Edit title' with a meeting that does not belong to them." in str(raised.value)
    assert " Meeting id: 123, user id: 1" in str(raised.value)
    context.api.assert_edit_message_not_called()


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

    view = meeting_views.edit_view(meeting).with_context(
        MeetingEditContentMessages.TITLE_SUCCESS.get(title=rich_title(meeting))
    )
    context.api.assert_send_message_called(update, view)
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# TITLE_MESSAGE — the title character cap
# ---------------------------------------------------------------------------


OVER_CAP_TITLE = "t" * (limits.TITLE_MAX_CHARS + 1)


@pytest.mark.parametrize("update", [UpdateRequest(message_text=OVER_CAP_TITLE)], indirect=True)
async def test_over_cap_title_leaves_the_meeting_untouched_and_reprompts(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics: MetricAssertions,
):
    """A title past the cap never reaches the meeting, and the prompt comes back with the error on
    top so the buttons stay reachable.

    This is the one place the shared rejection line and its metric are pinned in full; the other
    capped fields only prove they name themselves on it.
    """
    caplog.set_level(logging.INFO)
    meeting = user_with_settings.meetups[0]
    stored_title = meeting.title
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.TITLE_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TITLE: meeting.db_id},
    )

    assert meeting.title == stored_title
    assert state == ConversationMeetingState.EDIT_TITLE
    # The meeting id survives the refusal, so a shorter retry edits this same meeting.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_TITLE)
    # Nothing was published: the cards in other chats still show the stored title.
    context.api.assert_method_just_called("update_meeting_messages", times=0)

    error = MeetingEditContentMessages.TITLE_TOO_LONG.get(
        lang=user_with_settings.lang, length=len(OVER_CAP_TITLE), limit=limits.TITLE_MAX_CHARS
    )
    # Both numbers reach the reader: a message still carrying `${length}` would leave them guessing.
    assert str(len(OVER_CAP_TITLE)) in error.text
    assert str(limits.TITLE_MAX_CHARS) in error.text
    assert "${" not in error.text
    context.api.assert_send_message_called(
        update, edit_title_prompt_view(meeting, user_with_settings.lang, error=error)
    )

    record = log_record(caplog, "Meeting edit input rejected")
    assert record.levelname == "INFO"
    assert record.__dict__["field"] == "title"
    assert record.__dict__["reason"] == "too_long"
    assert record.__dict__["input_length"] == len(OVER_CAP_TITLE)
    assert record.__dict__["limit"] == limits.TITLE_MAX_CHARS
    # The refused title is the user's own text and never travels onto the line.
    assert OVER_CAP_TITLE not in caplog.text
    metrics.assert_emitted(name=MetricKey.ERROR, dimensions={"Feature": str(Feature.EDIT_MEETING)}, times=1)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="t" * limits.TITLE_MAX_CHARS)], indirect=True)
async def test_title_at_the_cap_is_stored(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """The cap is inclusive: a title of exactly the maximum length is a normal edit."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    _, state = await call_handler(
        EditMeetingHandlerId.TITLE_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_TITLE: meeting.db_id},
    )

    assert meeting.title == "t" * limits.TITLE_MAX_CHARS
    assert state == ConversationHandler.END
