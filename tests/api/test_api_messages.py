from unittest import mock

import pytest

from mitup_bot.api import edit_message, edit_message_view, send_message, send_message_view
from mitup_bot.views import MitupView


@pytest.mark.asyncio
async def test_send_message():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_chat.id = 123456789

    await send_message(context, update, message)

    context.bot.send_message.assert_called_once_with(chat_id=123456789, text="Hello, World", parse_mode="MarkdownV2")


@pytest.mark.asyncio
async def test_edit_message():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_chat.id = 123456789
    update.effective_message.message_id = 123

    await edit_message(context, update, message)

    assert update.effective_message is not None
    context.bot.edit_message_text.assert_called_once_with(
        "Hello, World", 123456789, message_id=123, parse_mode="MarkdownV2"
    )


@pytest.mark.asyncio
async def test_edit_message_without_effective_message():
    context = mock.AsyncMock()
    update = mock.MagicMock()

    message = "Hello, World"
    update.effective_message = None

    with pytest.raises(RuntimeError):
        await edit_message(context, update, message)


@pytest.mark.asyncio
async def test_send_message_view(default_view: MitupView):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789

    await send_message_view(context, update, default_view)

    context.bot.send_message.assert_called_once_with(
        chat_id=123456789, text=default_view.description, reply_markup=default_view.markup, parse_mode="MarkdownV2"
    )


@pytest.mark.asyncio
async def test_edit_message_view(default_view: MitupView):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat.id = 123456789
    update.effective_message.message_id = 123

    await edit_message_view(context, update, default_view)

    assert update.effective_message is not None
    context.bot.edit_message_text.assert_called_once_with(
        default_view.description, 123456789, message_id=123, reply_markup=default_view.markup, parse_mode="MarkdownV2"
    )


@pytest.mark.asyncio
async def test_edit_message_view_without_effective_message(default_view: MitupView):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_message = None

    with pytest.raises(RuntimeError):
        await edit_message_view(context, update, default_view)


@pytest.mark.asyncio
async def test_any_api_view_messages_fails_without_effective_chat(api_view_method, default_view):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await api_view_method(context, update, default_view)


@pytest.mark.asyncio
async def test_any_api_messages_without_view_fails_without_effective_chat(api_method):
    context = mock.AsyncMock()
    update = mock.MagicMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await api_method(context, update, "Hello, World")