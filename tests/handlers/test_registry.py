from enum import Enum, auto
from unittest import mock

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ConversationHandler
from telegram.ext.filters import PHOTO, TEXT, BaseFilter

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.registry import HandlerWrapper, callback_query_fallback
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.monitoring.metrics import NULL_DIMENSIONALITY
from mitup_bot.utils import callbacks as cb
from tests.helpers import AnyFloat, StubMitupApp, StubMitupContext, UpdateRequest, build_context
from tests.helpers.stub_db import MockDbSession  # sourcery skip: dont-import-test-modules


class ClearableRegistry(HandlersRegistry):
    handlers: dict[HandlerId, HandlerWrapper] = {}

    @classmethod
    def clear(cls):
        cls.handlers = {}


class HandlerTestId(HandlerId):
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
    async def command_not_bindable(update: Update, context: StubMitupContext):
        return "Done!"

    @ClearableRegistry.register_command(HandlerTestId.BINDABLE, bindable=True)
    async def command_bindable(update: Update, context: StubMitupContext):
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
    async def command(update: Update, context: StubMitupContext):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_command(HandlerTestId.BINDABLE, bindable=True, command=command_names[1])
        async def another_command(update: Update, context: StubMitupContext):
            return "Done!"

    ClearableRegistry.clear()


@pytest.mark.parametrize(
    "filters",
    [(TEXT, TEXT), (TEXT, PHOTO)],
    ids=["with_same_filter", "with_different_filter"],
)
def test_cannot_register_same_message_twice(filters: tuple[BaseFilter, BaseFilter]):
    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=filters[0])
    async def message(update: Update, context: StubMitupContext):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_message(HandlerTestId.BINDABLE, bindable=True, filters=filters[1])
        async def another_message(update: Update, context: StubMitupContext):
            return "Done!"

    ClearableRegistry.clear()


@pytest.mark.parametrize(
    "cbs",
    [(cb.SETTINGS, cb.SETTINGS), (cb.SETTINGS, cb.MAIN_MENU)],
    ids=["with_same_cb_data", "with_different_cb_data"],
)
def test_cannot_register_same_callback_query_twice(cbs: tuple[CallbackData, CallbackData]):
    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, callback_data=cbs[0])
    async def message(update: Update, context: StubMitupContext):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, callback_data=cbs[1])
        async def another_message(update: Update, context: StubMitupContext):
            return "Done!"

    ClearableRegistry.clear()


def test_cannot_register_same_conversation_twice():
    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND)
    async def command_something(update: Update, context: StubMitupContext):
        pass

    ClearableRegistry.register_conversation_handler(
        HandlerTestId.BINDABLE, entry_points_handler_names=[], states={}, fallbacks=[]
    )

    with pytest.raises(HandlerRegisteredError):
        ClearableRegistry.register_conversation_handler(
            HandlerTestId.BINDABLE, entry_points_handler_names=[], states={}, fallbacks=[]
        )

    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_callback_query_fallback_answers_with_sorry_message(
    update: Update,
    app: StubMitupApp,
):
    """callback_query_fallback calls bot.answer_callback_query with the fallback message."""
    context = build_context(update, app)
    assert update.callback_query is not None

    await callback_query_fallback(update, context)

    # The bot's answer_callback_query method is called with the fallback message and show_alert=True
    context.bot.answer_callback_query.assert_called_once_with(
        update.callback_query.id,
        "Sorry, I don't understand that yet.\nThis feature will be available soon! Stay tuned! 😄🚀",
        show_alert=True,
    )


def test_cannot_register_same_inline_handler_twice():
    """Registering the same handler_id via register_inline_handler twice raises HandlerRegisteredError."""

    @ClearableRegistry.register_inline_handler(HandlerTestId.BINDABLE)
    async def my_inline_handler(update: Update, context: StubMitupContext):
        pass

    with pytest.raises(HandlerRegisteredError):

        @ClearableRegistry.register_inline_handler(HandlerTestId.BINDABLE)
        async def another_inline_handler(update: Update, context: StubMitupContext):
            pass

    ClearableRegistry.clear()


async def test_all_handlers_emit_global_metrics(
    app: StubMitupApp, update: Update, context: StubMitupContext, mock_session: MockDbSession
):
    await app.initialize()

    # Define check_stat that is valid for conversation handlers
    check_state = [ConversationHandler.END, None, mock.AsyncMock(return_value=ConversationHandler.END), True]

    # Handle update with all handlers
    valid_handlers = 0

    global_logger = context.metrics_engine.get_logger(NULL_DIMENSIONALITY)
    for wrapper in HandlersRegistry.handlers.values():
        # Ignore conversation handlers because those are never executed, only handlers registered in them
        if wrapper.is_conversation:
            continue

        valid_handlers += 1

        handler_context = build_context(update, app)
        await wrapper.handler.handle_update(update, app, check_state, handler_context)
        # Keep track of all metrics emitted in each context for no dimension metrics

        logger = handler_context.metrics_engine.get_logger(NULL_DIMENSIONALITY)
        global_logger.sink.container.extend(logger.sink.container)

    # All handlers have emitted the global fault and time metric
    context.metrics_engine.assert_metrics_emited(
        [MetricKey.TIME, MetricKey.FAULT],
        [AnyFloat(), AnyFloat()],
        [Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=False,
        times=valid_handlers,
    )
