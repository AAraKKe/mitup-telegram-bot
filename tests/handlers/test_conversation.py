import pytest

from telegram import Update
from telegram.ext import ContextTypes

from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.conversations_states import Conversation_Settings_State
from mitup_bot.handlers.exceptions import HandlerNotRegistered


def test_conversation_fails_without_existing_handler():
    @HandlersRegistry.register_command("new_command_registered")
    async def command_custom_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.register_conversation_handler(
            "conversetion_with_no_handlers",
            entry_points_handler_names=["new_command_registered"],
            states={
                Conversation_Settings_State.TIMEZONE: ["new_command_not_registered"],
            },
            fallbacks=["new_command_registered"],
        )


def test_conversation_handler_can_be_registered():
    @HandlersRegistry.register_command("new_command_conversation_registered")
    async def command_conversation_registered(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    HandlersRegistry.register_conversation_handler(
        "conversation_with_handlers",
        entry_points_handler_names=["new_command_conversation_registered"],
        states={
            Conversation_Settings_State.TIMEZONE: ["new_command_conversation_registered"],
        },
        fallbacks=["new_command_conversation_registered"],
    )

    assert "conversation_with_handlers" in HandlersRegistry.handlers
