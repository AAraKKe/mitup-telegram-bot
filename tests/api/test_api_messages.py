from typing import cast
from unittest import mock

import pytest
from telegram import Update
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
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.views import MitupView
from tests.helpers import AnyFloat, StubMitupContext, create_meetup
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


async def assert_time_metric_emitted(context: StubMitupContext, metrics: MetricAssertions, times: int = 1):
    await context.metrics.flush()

    metrics.assert_emitted(
        name="TelegramApiTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
        times=times,
    )


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

    await assert_time_metric_emitted(context, metrics)


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

    await assert_time_metric_emitted(context, metrics)


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

    await assert_time_metric_emitted(context, metrics)


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

    await assert_time_metric_emitted(context, metrics)


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

    await assert_time_metric_emitted(context, metrics)


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

    await assert_time_metric_emitted(context, metrics)


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
    inline_view = meeting.inline_view()
    main_view = meeting.main_view()
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

    await assert_time_metric_emitted(context, metrics, times=3)  # one metric per edit call


@pytest.mark.parametrize("bad_request_message", [pat.pattern for pat in MESSAGE_NOT_FOUND_ERROR_PATTERNS])
async def test_edit_meetup_messages_counts_dead_message_and_continues(
    meeting: Meetup,
    context: StubMitupContext,
    bad_request_message: str,
    metrics: MetricAssertions,
):
    """A user-deleted message is counted and skipped; the other messages are still edited.

    The stale row's DB cleanup is the write lifecycle's reconcile job (see
    tests/test_api_wrapper.py::test_execute_queued_records_dead_message_for_reconcile) —
    immediate mode only emits the metric.
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

    await context.api.update_meeting_messages(meeting=meeting)
    # Since this is outside a callback, make sure we flush metrics
    await context.metrics.flush()

    assert edit.call_count == 2

    metrics.assert_emitted(name=MetricKey.MESSAGE_DELETED, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(
        name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2
    )  # one per edit attempt


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

    await assert_time_metric_emitted(context, metrics, times=2)  # one metric per edit call
