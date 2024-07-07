from typing import cast
from unittest import mock

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import InlineKeyboardMarkup, Update
from telegram.error import BadRequest

from mitup_bot.api import edit_message, send_message, update_meeting_messages
from mitup_bot.exceptions import EffectiveChatNotSet, NoMessageAvailable
from mitup_bot.models import Meetup, Message, MessageButtons, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView
from tests.helpers import StubMitupContext, UpdateRequest
from tests.stub_db import MockDbSession  # sourcery skip: dont-import-test-modules


async def test_edit_message_without_message_available():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None
    update.callback_query = None

    with pytest.raises(NoMessageAvailable):
        await edit_message(context=context, update=update, view=message)


async def test_edit_message_without_inline_message_id():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None
    update.callback_query.inline_message_id = None

    with pytest.raises(NoMessageAvailable):
        await edit_message(context=context, update=update, view=message)


async def test_send_message_with_a_view(default_view: MitupView):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789

    await send_message(context=context, update=update, view=default_view)

    context.bot.send_message.assert_called_once_with(
        chat_id=123456789, text=default_view.description, reply_markup=default_view.markup
    )


async def test_send_message_without_view():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789

    await send_message(context=context, update=update, view="Hello, World")

    context.bot.send_message.assert_called_once_with(chat_id=123456789, text="Hello, World", reply_markup=None)


async def test_edit_message_with_a_view(default_view: MitupView, update: Update, context: StubMitupContext):
    assert update.effective_message is not None

    await edit_message(context=cast(TMitupContext, context), update=update, view=default_view)

    context.bot.edit_message_text.assert_called_once_with(
        text=default_view.description,
        chat_id=123,
        message_id=123,
        inline_message_id=None,
        reply_markup=default_view.markup,
    )


async def test_edit_message_without_view(update: Update, context: StubMitupContext):
    await edit_message(context=cast(TMitupContext, context), update=update, view="Hello, World")

    context.bot.edit_message_text.assert_called_once_with(
        text="Hello, World", chat_id=123, message_id=123, inline_message_id=None, reply_markup=None
    )


@pytest.mark.parametrize("update", [UpdateRequest(message=False)], indirect=True)
async def test_send_message_fails_without_effective_chat(
    default_view: MitupView, update: Update, context: StubMitupContext
):
    with pytest.raises(EffectiveChatNotSet):
        await send_message(context=cast(TMitupContext, context), update=update, view=default_view)


async def test_edit_meetup_messages(user_with_settings: User, context: StubMitupContext, mock_session: MockDbSession):
    meeting = Meetup(id=123, owner=user_with_settings, title="Test meeting", description="Test description")
    # Message in the chat with the owner
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    # Inline message shared somewhere
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))
    # Message in the chat of someone who is not the owner
    meeting.messages.append(Message(id=456, message_id=123, chat_id=234, buttons=buttons))

    await update_meeting_messages(session=mock_session, context=cast(TMitupContext, context), meeting=meeting)

    edit: mock.MagicMock = context.bot.edit_message_text
    expected_call_params = {
        "text": meeting.inline_view.description,
        "chat_id": 123,
        "message_id": None,
        "inline_message_id": None,
        "reply_markup": InlineKeyboardMarkup([]),
    }

    assert edit.call_count == 3
    edit.assert_has_calls(
        [
            mock.call(**(expected_call_params | {"text": meeting.main_view.description, "message_id": 123})),
            mock.call(
                **(
                    expected_call_params
                    | {
                        "inline_message_id": "456",
                        "reply_markup": MitupView.keyboard_to_markup(buttons.keyboard),
                    }
                )
            ),
            mock.call(
                **(
                    expected_call_params
                    | {
                        "text": meeting.inline_view.description,
                        "message_id": 123,
                        "chat_id": 234,
                        "reply_markup": MitupView.keyboard_to_markup(buttons.keyboard),
                    }
                )
            ),
        ]
    )


async def test_edit_meetup_messages_deletes_message_on_failure(
    meeting: Meetup, context: StubMitupContext, mock_session: MockDbSession
):
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))

    edit: mock.MagicMock = context.bot.edit_message_text

    # Make the call fail for one call, we should delete the message but still edit properly the other one
    def raise_error(*args, **kwargs):
        if kwargs.get("message_id") == 123:
            raise BadRequest("Message_id_invalid")

    edit.side_effect = raise_error

    await update_meeting_messages(session=mock_session, context=cast(TMitupContext, context), meeting=meeting)
    # Since this is outside a callback, make sure we flush metrics
    await context.flush_metrics()

    assert edit.call_count == 2
    mock_session.assert_deleted(meeting.messages[0])
    context.metrics.assert_metrics_emited(
        [MetricKey.MESSAGE_DELETED], [1], [Unit.COUNT], add_handler_dimensions=False, add_update_properties=False
    )


async def test_edit_meetup_messages_ignore_unchanged_message(
    meeting: Meetup, context: StubMitupContext, mock_session: MockDbSession
):
    meeting.messages.append(Message(id=123, message_id=123, chat_id=123))
    buttons = MessageButtons(
        keyboard=[[ButtonConfig(text="Text1", callback_data="cb1"), ButtonConfig(text="Text2", callback_data="cb2")]]
    )
    meeting.messages.append(Message(id=456, inline_message_id="456", chat_id=123, buttons=buttons))

    edit: mock.MagicMock = context.bot.edit_message_text

    # Make the call fail for one call, we should delete the message but still edit properly the other one
    def raise_error(*args, **kwargs):
        if kwargs.get("message_id") == 123:
            raise BadRequest("Message is not modified")

    edit.side_effect = raise_error

    await update_meeting_messages(session=mock_session, context=cast(TMitupContext, context), meeting=meeting)

    assert edit.call_count == 2
