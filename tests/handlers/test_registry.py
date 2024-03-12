from enum import Enum, auto

import pytest
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.ext.filters import PHOTO, TEXT, BaseFilter

from mitup_bot.callback_data import CallbackData
from mitup_bot.handlers import CallbackId, HandlersRegistry
from mitup_bot.handlers.exceptions import HandlerRegisteredError
from mitup_bot.handlers.registry import HandlerWrapper
from mitup_bot.utils import callbacks as cb


class ClearableRegistry(HandlersRegistry):
    handlers: dict[CallbackId, HandlerWrapper] = {}

    @classmethod
    def clear(cls):
        cls.handlers = {}


class HandlerTestId(CallbackId):
    BINDABLE = auto()
    NOT_BINDABLE = auto()
    ENTRY_POINT = auto()
    SOME_COMMAND = auto()


class ConversationStates(Enum):
    STATE_ONE = auto()


def test_registry_has_handlers():
    assert len(HandlersRegistry.handlers) > 0


def test_handlers_registered_when_bound_to_app():
    # Given some application
    app = ApplicationBuilder().token("AAA").build()

    # With no handlers to begin with
    assert len(app.handlers) == 0

    # When we bind it with the registry
    HandlersRegistry.bind(app)

    # The app now has those handlers
    assert len(app.handlers) > 0


def test_only_bindable_handlers_are_registered():
    @ClearableRegistry.register_command(HandlerTestId.NOT_BINDABLE, bindable=False)
    async def command_not_bindable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    @ClearableRegistry.register_command(HandlerTestId.BINDABLE, bindable=True)
    async def command_bindable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    app = ApplicationBuilder().token("AAA").build()
    ClearableRegistry.bind(app)
    command_handlers = [
        next(iter(handler.commands))
        for handler_list in app.handlers.values()
        for handler in handler_list
        if isinstance(handler, CommandHandler)
    ]

    assert "bindable" in command_handlers
    assert "not_bindable" not in command_handlers
    assert HandlerTestId.NOT_BINDABLE in ClearableRegistry.handlers
    assert HandlerTestId.BINDABLE in ClearableRegistry.handlers
    ClearableRegistry.clear()


@pytest.mark.parametrize(
    "command_names",
    [("name", "name"), ("name", "other_name")],
    ids=["with_same_name", "with_different_name"],
)
def test_cannot_register_same_command_twice(command_names: tuple[str, str]):
    @ClearableRegistry.register_command(HandlerTestId.BINDABLE, bindable=True, command=command_names[0])
    async def command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_command(HandlerTestId.BINDABLE, bindable=True, command=command_names[1])
        async def another_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"

    ClearableRegistry.clear()


@pytest.mark.parametrize(
    "filters",
    [(TEXT, TEXT), (TEXT, PHOTO)],
    ids=["with_same_filter", "with_different_filter"],
)
def test_cannot_register_same_message_twice(filters: tuple[BaseFilter, BaseFilter]):
    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=filters[0])
    async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_message(HandlerTestId.BINDABLE, bindable=True, filters=filters[1])
        async def another_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"

    ClearableRegistry.clear()


@pytest.mark.parametrize(
    "cbs",
    [(cb.SETTINGS, cb.SETTINGS), (cb.SETTINGS, cb.MAIN_MENU)],
    ids=["with_same_cb_data", "with_different_cb_data"],
)
def test_cannot_register_same_callback_query_twice(cbs: tuple[CallbackData, CallbackData]):
    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, callback_data=cbs[0])
    async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, callback_data=cbs[1])
        async def another_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return "Done!"

    ClearableRegistry.clear()


def test_cannot_register_same_conversation_twice():
    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND)
    async def command_something(update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    ClearableRegistry.register_conversation_handler(
        HandlerTestId.BINDABLE, entry_points_handler_names=[], states={}, fallbacks=[]
    )

    with pytest.raises(HandlerRegisteredError):
        ClearableRegistry.register_conversation_handler(
            HandlerTestId.BINDABLE, entry_points_handler_names=[], states={}, fallbacks=[]
        )

    ClearableRegistry.clear()
