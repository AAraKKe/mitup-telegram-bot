from typing import cast
from unittest import mock
from unittest.mock import MagicMock

import pytest
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from telegram.ext.filters import CAPTION, PHOTO

from mitup_bot.handlers import ConversationSettingsState, HandlersRegistry
from mitup_bot.handlers.commands import command_cancel, command_start_with_existing_user, command_start_with_new_user
from mitup_bot.handlers.exceptions import HandlerNotRegistered, HandlerRegisteredError, WrongCommandNameError


async def test_command_registry_can_register_command_handlers():
    @HandlersRegistry.register_command("my_command")
    async def command_my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert "my_command" in HandlersRegistry.handlers
    handler = HandlersRegistry.get_handler("my_command")
    assert isinstance(handler, CommandHandler)
    assert handler.has_args is None
    assert "my_command" in handler.commands

    callback_return = await handler.callback(Update(0), None)
    assert callback_return == "Done!"


async def test_register_command_with_custom_name():
    @HandlersRegistry.register_command("the_custom_command_name", command="my_custom_command")
    async def command_my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert "the_custom_command_name" in HandlersRegistry.handlers


async def test_register_command_with_filters():
    @HandlersRegistry.register_command("with_filters", filters=CAPTION & PHOTO)
    async def command_with_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert "with_filters" in HandlersRegistry.handlers


def test_registry_raises_if_command_name_is_not_correct():
    # Should raise error if we want to register a CommandHandler with a callback name
    # that does not start with `command_`
    with pytest.raises(WrongCommandNameError):

        @HandlersRegistry.register_command("wrong_command_name")
        async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_registry_raises_if_hander_already_registered():
    @HandlersRegistry.register_command("existing_command")
    async def command_my_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @HandlersRegistry.register_command("existing_command")
        async def command_existing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_multiple_commands_can_be_registered_with_different_names():
    @HandlersRegistry.register_command("some_custom_command")
    async def command_custom_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    @HandlersRegistry.register_command("new_custom_command", command="custom_new", filters=CAPTION)
    async def command_another_custom_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert "some_custom_command" in HandlersRegistry.handlers
    assert "new_custom_command" in HandlersRegistry.handlers

    # Check the command is the same in both
    custom_command_handler = cast(CommandHandler, HandlersRegistry.get_handler("some_custom_command"))
    new_custom_command_handler = cast(CommandHandler, HandlersRegistry.get_handler("new_custom_command"))
    assert custom_command_handler.commands == new_custom_command_handler.commands


def test_registry_fails_to_get_handler_that_does_not_exist():
    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.get_handler("I_do_not_exist")


@pytest.mark.asyncio
async def test_command_start_with_new_user(mock_session: MagicMock):
    update = MagicMock()
    context = MagicMock()

    update.effective_user.first_name = "John"
    update.effective_user.last_name = "Doe"
    update.effective_user.id = 123456789
    update.effective_user.username = "johndoe"

    with mock.patch("mitup_bot.handlers.commands.send_message") as mock_send_message:
        result = await command_start_with_new_user(update, context)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert update.effective_user is not None
        mock_send_message.assert_called_once_with(
            context, update, "Welcome to Mitup Bot John! Please, tell me your timezone."
        )
        assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
async def test_command_start_with_existing_user():
    update = MagicMock()
    context = MagicMock()

    with mock.patch("mitup_bot.handlers.commands.send_message_view") as mock_send_message_view:
        await command_start_with_existing_user(update, context)

        mock_send_message_view.assert_called_once()


@pytest.mark.asyncio
async def test_command_cancel():
    update = MagicMock()
    context = MagicMock()

    with mock.patch("mitup_bot.handlers.commands.send_message") as mock_send_message:
        await command_cancel(update, context)

        mock_send_message.assert_called_once()


@pytest.mark.asyncio
async def test_any_command_fails_without_effective_chat(command_list):
    update = MagicMock()
    context = MagicMock()

    update.effective_chat = None

    with pytest.raises(RuntimeError):
        await command_list(update, context)
