import pytest
from telegram import Update

from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import HandlerNotRegistered
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.conversations_states import ConversationSettingsState


class CommandsTestId(CallbackId):
    COMMAND_REGISTERED = "new_command_registered"
    COMMAND_REGISTERED_2 = "other new command registered"
    COMMAND_NOT_REGISTERED = "not_registered"


class ConversationsTestId(CallbackId):
    CONVERSATION_WITH_NO_HANDLERS = "conversation_with_no_handlers"
    CONVERSATION_WITH_HANDLERS = "conversation_with_handlers"


def test_conversation_fails_without_existing_handler():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_REGISTERED)
    async def command_custom_new(update: Update, context: MitupContext):
        return "Done!"

    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.register_conversation_handler(
            ConversationsTestId.CONVERSATION_WITH_NO_HANDLERS,
            entry_points_handler_names=[CommandsTestId.COMMAND_REGISTERED],
            states={
                ConversationSettingsState.TIMEZONE: [CommandsTestId.COMMAND_NOT_REGISTERED],
            },
            fallbacks=[CommandsTestId.COMMAND_REGISTERED],
        )


def test_conversation_handler_can_be_registered():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_REGISTERED_2)
    async def command_conversation_registered(update: Update, context: MitupContext):
        return "Done!"

    HandlersRegistry.register_conversation_handler(
        ConversationsTestId.CONVERSATION_WITH_HANDLERS,
        entry_points_handler_names=[CommandsTestId.COMMAND_REGISTERED_2],
        states={
            ConversationSettingsState.TIMEZONE: [CommandsTestId.COMMAND_REGISTERED_2],
        },
        fallbacks=[CommandsTestId.COMMAND_REGISTERED_2],
    )

    assert ConversationsTestId.CONVERSATION_WITH_HANDLERS in HandlersRegistry.handlers
