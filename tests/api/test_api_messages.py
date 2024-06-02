from unittest import mock

import pytest
from telegram import Update

from mitup_bot.api import edit_message, send_message
from mitup_bot.exceptions import EffectiveChatNotSet, EffectiveMessageNotSet
from mitup_bot.views import MitupView
from tests.helpers import StubMitupContext, UpdateRequest


async def test_edit_message_without_effective_message():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None

    with pytest.raises(RuntimeError):
        await edit_message(context, update, message)


async def test_send_message_with_a_view(default_view: MitupView):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789

    await send_message(context, update, default_view)

    context.bot.send_message.assert_called_once_with(
        chat_id=123456789, text=default_view.description, reply_markup=default_view.markup
    )


async def test_send_message_without_view():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789

    await send_message(context, update, "Hello, World")

    context.bot.send_message.assert_called_once_with(chat_id=123456789, text="Hello, World", reply_markup=None)


async def test_edit_message_with_a_view(default_view: MitupView, update: Update, context: StubMitupContext):
    assert update.effective_message is not None

    await edit_message(context, update, default_view)

    context.bot.edit_message_text.assert_called_once_with(
        default_view.description, 123, message_id=123, reply_markup=default_view.markup
    )


async def test_edit_message_without_view(update: Update, context: StubMitupContext):
    await edit_message(context, update, "Hello, World")

    context.bot.edit_message_text.assert_called_once_with("Hello, World", 123, message_id=123, reply_markup=None)


@pytest.mark.parametrize("update", [UpdateRequest(message=False)], indirect=True)
async def test_edit_message_fails_without_effective_message(
    default_view: MitupView, update: Update, context: StubMitupContext
):
    with pytest.raises(EffectiveMessageNotSet):
        await edit_message(context, update, default_view)


@pytest.mark.parametrize("update", [UpdateRequest(message=False)], indirect=True)
async def test_send_message_fails_without_effective_chat(
    default_view: MitupView, update: Update, context: StubMitupContext
):
    with pytest.raises(EffectiveChatNotSet):
        await send_message(context, update, default_view)
