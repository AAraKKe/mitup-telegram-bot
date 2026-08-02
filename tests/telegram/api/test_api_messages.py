from typing import cast
from unittest import mock

import pytest
from structlog.testing import capture_logs
from telegram import MessageEntity, Update
from telegram.error import BadRequest

from mitup_bot.api_wrapper import (
    EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS,
    MESSAGE_NOT_FOUND_ERROR_PATTERNS,
    ContextOrBotAdapter,
    TelegramApi,
)
from mitup_bot.exceptions import NoMessageAvailable
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, Message, MessageButtons, User
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from tests.helpers import StubMitupContext, create_meetup
from tests.helpers.context import build_context
from tests.helpers.monitoring import MetricAssertions


@pytest.fixture
def context(app, update, metrics_client: MetricsClient):
    """Override the global context fixture and inject real API wrapper."""
    context = build_context(update, app, metrics=metrics_client)
    # We want to test the real wrapper here, not the mock
    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, context)
    context.api = api  # ty: ignore[invalid-assignment]  # nolink: intentional — test fixture assigns real TelegramApi to a MockApi-typed field
    return context


async def test_edit_message_without_message_available(context: StubMitupContext):
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None
    update.callback_query = None

    with pytest.raises(NoMessageAvailable):
        await context.api.edit_message(update=update, view=message)


async def test_edit_message_without_inline_message_id(context: StubMitupContext):
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None
    update.callback_query.inline_message_id = None

    with pytest.raises(NoMessageAvailable):
        await context.api.edit_message(update=update, view=message)


async def test_clear_reply_markup_raises_no_message_available_with_callback_query_without_inline_id(
    context: StubMitupContext,
):
    """Raises NoMessageAvailable when callback_query exists but both message refs are None."""
    update = mock.MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query = mock.MagicMock()
    update.callback_query.inline_message_id = None
    update.callback_query.message = None

    with pytest.raises(NoMessageAvailable):
        await context.api.clear_reply_markup(update)


async def test_send_message_with_a_view(
    context: StubMitupContext, update: Update, default_view: MitupView, metrics: MetricAssertions
):
    assert context.telegram_update.effective_chat is not None

    await context.api.send_message(update=update, view=default_view)

    context.bot.send_message.assert_called_once_with(
        chat_id=context.telegram_update.effective_chat.id,
        text=default_view.description.text,
        entities=None,
        reply_markup=default_view.markup,
        disable_web_page_preview=True,
    )


async def test_send_message_without_view(context: StubMitupContext, update: Update, metrics: MetricAssertions):
    assert context.telegram_update.effective_chat is not None

    await context.api.send_message(update=update, view="Hello, World")

    context.bot.send_message.assert_called_once_with(
        chat_id=context.telegram_update.effective_chat.id,
        text="Hello, World",
        entities=None,
        reply_markup=None,
        disable_web_page_preview=True,
    )


async def test_send_message_with_entities(
    context: StubMitupContext, update: Update, view_with_entities: MitupView, metrics: MetricAssertions
):
    assert context.telegram_update.effective_chat is not None

    await context.api.send_message(update=update, view=view_with_entities)

    context.bot.send_message.assert_called_once_with(
        chat_id=context.telegram_update.effective_chat.id,
        text=view_with_entities.description.text,
        entities=view_with_entities.description.entities,
        reply_markup=view_with_entities.markup,
        disable_web_page_preview=True,
    )


async def test_edit_message_with_entities(
    view_with_entities: MitupView, update: Update, context: StubMitupContext, metrics: MetricAssertions
):
    assert update.effective_message is not None

    await context.api.edit_message(update=update, view=view_with_entities)

    context.bot.edit_message_text.assert_called_once_with(
        text=view_with_entities.description.text,
        entities=view_with_entities.description.entities,
        chat_id=123,
        message_id=123,
        inline_message_id=None,
        reply_markup=view_with_entities.markup,
        disable_web_page_preview=True,
    )


async def test_edit_message_with_a_view(
    default_view: MitupView, update: Update, context: StubMitupContext, metrics: MetricAssertions
):
    assert update.effective_message is not None

    await context.api.edit_message(update=update, view=default_view)

    context.bot.edit_message_text.assert_called_once_with(
        text=default_view.description.text,
        entities=None,
        chat_id=123,
        message_id=123,
        inline_message_id=None,
        reply_markup=default_view.markup,
        disable_web_page_preview=True,
    )


async def test_edit_message_without_view(update: Update, context: StubMitupContext, metrics: MetricAssertions):
    await context.api.edit_message(update=update, view="Hello, World")

    context.bot.edit_message_text.assert_called_once_with(
        text="Hello, World",
        entities=None,
        chat_id=123,
        message_id=123,
        inline_message_id=None,
        reply_markup=None,
        disable_web_page_preview=True,
    )


async def test_edit_meetup_messages(user_with_settings: User, context: StubMitupContext, metrics: MetricAssertions):
    meeting = create_meetup(id=123, owner=user_with_settings, title="Test meeting", description="Test description")
    # Message in the chat with the owner
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    # Inline message shared somewhere
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))
    # Message in the chat of someone who is not the owner
    meeting.messages.append(Message(id=456, message_id=123, chat_id=234, buttons=buttons))

    await context.api.update_meeting_messages(meeting=meeting)

    edit: mock.MagicMock = context.bot.edit_message_text
    inline_view = meeting_views.inline_view(meeting)
    main_view = meeting_views.main_view(meeting)
    expected_call_params = {
        "text": inline_view.description.text,
        "entities": inline_view.description.entities or None,
        "chat_id": 123,
        "message_id": None,
        "inline_message_id": None,
        "reply_markup": inline_view.markup,
        "disable_web_page_preview": True,
    }

    assert edit.call_count == 3
    edit.assert_has_calls(
        [
            mock.call(
                **(
                    expected_call_params
                    | {
                        "text": main_view.description.text,
                        "entities": main_view.description.entities or None,
                        "message_id": 123,
                        "reply_markup": main_view.markup,
                    }
                )
            ),
            mock.call(
                **(
                    expected_call_params
                    | {
                        "inline_message_id": "456",
                    }
                )
            ),
            mock.call(
                **(
                    expected_call_params
                    | {
                        "text": inline_view.description.text,
                        "entities": inline_view.description.entities or None,
                        "message_id": 123,
                        "chat_id": 234,
                    }
                )
            ),
        ]
    )


@pytest.mark.parametrize("bad_request_message", [pat.pattern for pat in MESSAGE_NOT_FOUND_ERROR_PATTERNS])
async def test_edit_meetup_messages_records_dead_message_and_continues(
    meeting: Meetup,
    context: StubMitupContext,
    bad_request_message: str,
):
    """A user-deleted message is recorded and skipped; the other messages are still edited.

    The stale row's DB cleanup is the write lifecycle's reconcile job (see
    tests/test_api_wrapper.py::test_execute_queued_records_dead_message_for_reconcile) —
    immediate mode only writes the line.
    """
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))

    edit: mock.MagicMock = context.bot.edit_message_text

    def raise_error(*args, **kwargs):
        if kwargs.get("message_id") == 123:
            raise BadRequest(bad_request_message)

    edit.side_effect = raise_error

    with capture_logs() as logs:
        await context.api.update_meeting_messages(meeting=meeting)
    # Since this is outside a callback, make sure we flush metrics
    await context.metrics.flush()

    assert edit.call_count == 2

    dropped = [entry for entry in logs if entry["event"] == "Meeting message unreachable, dropping it"]
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "message_not_found"


@pytest.mark.parametrize("bad_request_message", [pat.pattern for pat in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS])
async def test_edit_meetup_messages_ignore_unchanged_message(
    meeting: Meetup,
    context: StubMitupContext,
    bad_request_message: str,
    metrics: MetricAssertions,
):
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))

    edit: mock.MagicMock = context.bot.edit_message_text

    # Make the call fail for one call, the other one should still be edited properly
    def raise_error(*args, **kwargs):
        if kwargs.get("message_id") == 123:
            raise BadRequest(bad_request_message)

    edit.side_effect = raise_error

    await context.api.update_meeting_messages(meeting=meeting)

    assert edit.call_count == 2


CUSTOM_EMOJI_ENTITY = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="123456")
BOLD_ENTITY = MessageEntity(type=MessageEntity.BOLD, offset=3, length=4)


async def test_send_message_retries_without_custom_emoji_on_rejection(context: StubMitupContext, update: Update):
    context.bot.send_message.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]
    view = MitupView(FormattedText("😀 bold", [CUSTOM_EMOJI_ENTITY, BOLD_ENTITY]), [])

    await context.api.send_message(update=update, view=view)

    assert context.bot.send_message.call_count == 2
    retry_kwargs = context.bot.send_message.call_args_list[1].kwargs
    assert retry_kwargs["entities"] == [BOLD_ENTITY]
    assert retry_kwargs["text"] == "😀 bold"


async def test_send_message_retry_sends_no_entities_when_only_custom_emoji(context: StubMitupContext, update: Update):
    context.bot.send_message.side_effect = [BadRequest("can't parse custom emoji entity"), None]
    view = MitupView(FormattedText("😀", [CUSTOM_EMOJI_ENTITY]), [])

    await context.api.send_message(update=update, view=view)

    assert context.bot.send_message.call_count == 2
    assert context.bot.send_message.call_args_list[1].kwargs["entities"] is None


async def test_send_message_does_not_retry_when_error_is_not_about_custom_emoji(
    context: StubMitupContext, update: Update
):
    context.bot.send_message.side_effect = BadRequest("Chat not found")
    view = MitupView(FormattedText("😀", [CUSTOM_EMOJI_ENTITY]), [])

    with pytest.raises(BadRequest):
        await context.api.send_message(update=update, view=view)

    assert context.bot.send_message.call_count == 1


async def test_send_message_does_not_retry_without_custom_emoji_entities(context: StubMitupContext, update: Update):
    context.bot.send_message.side_effect = BadRequest("Custom emoji entities are not allowed")
    view = MitupView(FormattedText("no emoji", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=2)]), [])

    with pytest.raises(BadRequest):
        await context.api.send_message(update=update, view=view)

    assert context.bot.send_message.call_count == 1


async def test_edit_message_retries_without_custom_emoji_on_rejection(context: StubMitupContext, update: Update):
    context.bot.edit_message_text.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]
    view = MitupView(FormattedText("😀 bold", [CUSTOM_EMOJI_ENTITY, BOLD_ENTITY]), [])

    await context.api.edit_message(update=update, view=view)

    assert context.bot.edit_message_text.call_count == 2
    assert context.bot.edit_message_text.call_args_list[1].kwargs["entities"] == [BOLD_ENTITY]


async def test_update_meeting_messages_retries_without_custom_emoji_on_rejection(
    user_with_settings: User, context: StubMitupContext
):
    meeting = create_meetup(id=123, owner=user_with_settings, title='Party <tg-emoji emoji-id="123456">😀</tg-emoji>')
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))

    edit: mock.MagicMock = context.bot.edit_message_text
    edit.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]

    await context.api.update_meeting_messages(meeting=meeting)

    assert edit.call_count == 2
    retry_entities = edit.call_args_list[1].kwargs["entities"]
    assert all(entity.type != MessageEntity.CUSTOM_EMOJI for entity in retry_entities)
