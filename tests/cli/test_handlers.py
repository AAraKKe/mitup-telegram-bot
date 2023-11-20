import pytest
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from mitup_bot.exceptions import (
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)
from mitup_bot.handlers import HandlersRegistry


def test_registry_has_handlers():
    assert len(HandlersRegistry.handlers) > 0


async def test_registry_can_register_command_handlers():
    @HandlersRegistry.register_command
    async def command_my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    assert "command_my_command" in HandlersRegistry.handlers
    handler = HandlersRegistry.get_handler("command_my_command")
    assert isinstance(handler, CommandHandler)
    assert handler.has_args is None
    assert "my_command" in handler.commands

    callback_return = await handler.callback(Update(0), None)
    assert "Done!" == callback_return


def test_registry_raises_if_command_name_is_not_correct():
    # Should raise error if we want to register a CommandHandler with a callback name
    # that does not start with `command_`
    with pytest.raises(WrongCommandNameError):

        @HandlersRegistry.register_command
        async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_registry_raises_if_hander_already_registered():
    # Should raise error if we are registering a command that already exist, in this case
    # the start command that will always exist
    with pytest.raises(HandlerRegisteredError):

        @HandlersRegistry.register_command
        async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"


def test_registry_failes_to_get_handler_that_does_not_exist():
    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.get_handler("I_do_not_exist")


def test_handlers_registered_when_bound_to_app():
    # Given some application
    app = ApplicationBuilder().token("AAA").build()

    # With no handlers to begin with
    assert len(app.handlers) == 0

    # When we bind it with the registry
    HandlersRegistry.bind(app)

    # The app now has those handlers
    assert len(app.handlers) > 0
