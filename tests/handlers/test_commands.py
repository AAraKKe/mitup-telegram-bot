from typing import cast
from unittest import mock
from unittest.mock import MagicMock

import pytest
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from telegram.ext.filters import CAPTION, PHOTO

from mitup_bot.callback_id import CallbackId
from mitup_bot.exceptions import HandlerNotRegistered, HandlerRegisteredError, WrongCommandNameError
from mitup_bot.handlers import ConversationSettingsState, HandlersRegistry
from mitup_bot.handlers.commands import (
    command_cancel,
    command_go_to_main_menu,
    command_start_with_existing_user,
    command_start_with_new_user,
)
from mitup_bot.utils import SettingsMessages


class CommandsTestId(CallbackId):
    COMMAND_EXAMPLE = "my_command"
    COMMAND_CUSTOM_NAME = "the_custom_command_name"
    COMMAND_WITH_FILTERS = "with_filters"
    COMMAND_WRONG_NAME = "wrong_command_name"
    COMMAND_EXIST_NAME = "existing_command"
    COMMAND_CUSTOM_NAME_2 = "some custom command"
    COMMAND_CUSTOM_NAME_3 = "new_custom_command"
    COMMAND_NOT_REGISTERED = "not_registered"


async def test_command_registry_can_register_command_handlers():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXAMPLE)
    async def command_my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert CommandsTestId.COMMAND_EXAMPLE in HandlersRegistry.handlers
    handler = HandlersRegistry.get_handler(CommandsTestId.COMMAND_EXAMPLE)
    assert isinstance(handler, CommandHandler)
    assert handler.has_args is None
    assert "my_command" in handler.commands

    callback_return = await handler.callback(Update(0), None)
    assert callback_return == "Done!"


async def test_register_command_with_custom_name():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME, command="my_custom_command")
    async def command_my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert CommandsTestId.COMMAND_CUSTOM_NAME in HandlersRegistry.handlers


async def test_register_command_with_filters():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_WITH_FILTERS, filters=CAPTION & PHOTO)
    async def command_with_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert CommandsTestId.COMMAND_WITH_FILTERS in HandlersRegistry.handlers


def test_registry_raises_if_command_name_is_not_correct():
    # Should raise error if we want to register a CommandHandler with a callback name
    # that does not start with `command_`
    with pytest.raises(WrongCommandNameError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_WRONG_NAME)
        async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_registry_raises_if_hander_already_registered():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
    async def command_my_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
        async def command_existing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_multiple_commands_can_be_registered_with_different_names():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_2)
    async def command_custom_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_3, command="custom_new", filters=CAPTION)
    async def command_another_custom_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert CommandsTestId.COMMAND_CUSTOM_NAME_2 in HandlersRegistry.handlers
    assert CommandsTestId.COMMAND_CUSTOM_NAME_3 in HandlersRegistry.handlers

    # Check the command is the same in both
    custom_command_handler = cast(CommandHandler, HandlersRegistry.get_handler(CommandsTestId.COMMAND_CUSTOM_NAME_2))
    new_custom_command_handler = cast(
        CommandHandler, HandlersRegistry.get_handler(CommandsTestId.COMMAND_CUSTOM_NAME_3)
    )
    assert custom_command_handler.commands == new_custom_command_handler.commands


def test_registry_fails_to_get_handler_that_does_not_exist():
    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.get_handler(CommandsTestId.COMMAND_NOT_REGISTERED)


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
        assert update.effective_user is not None
        mock_send_message.assert_called_once_with(
            context, update, SettingsMessages.SET_REGISTRATION_TIMEZONE.get(first_name="John")
        )
        assert result == ConversationSettingsState.TIMEZONE


@pytest.mark.asyncio
@pytest.mark.parametrize("command_list", [command_start_with_existing_user, command_go_to_main_menu])
async def test_commands_to_show_main_menu(command_list):
    update = MagicMock()
    context = MagicMock()

    with mock.patch("mitup_bot.handlers.commands.send_message") as mock_send_message:
        await command_start_with_existing_user(update, context)

        mock_send_message.assert_called_once()


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
