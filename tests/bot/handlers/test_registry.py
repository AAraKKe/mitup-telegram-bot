from contextlib import suppress
from enum import Enum, auto
from typing import Any, cast
from unittest import mock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop, CommandHandler, ConversationHandler
from telegram.ext.filters import PHOTO, TEXT, BaseFilter

from mitup_bot import db
from mitup_bot.api_wrapper import build_api
from mitup_bot.callback_data import CallbackData
from mitup_bot.config import Env
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.exceptions import HandlerNotRegistered, HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState
from mitup_bot.handlers.registry import HandlerWrapper, callback_query_fallback, callback_with_metrics
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.utils import callbacks as cb
from tests.helpers import (
    AnyFloat,
    MockApi,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    build_context,
    create_bot_config,
    make_test_metrics_client,
)
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.monitoring import MetricAssertions
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
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """callback_query_fallback answers with the fallback message and counts the unhandled interaction."""
    context = build_context(update, app, metrics=metrics_client)
    assert update.callback_query is not None

    await callback_query_fallback(update, context)

    # The bot's answer_callback_query method is called with the fallback message and show_alert=True
    context.bot.answer_callback_query.assert_called_once_with(
        update.callback_query.id,
        "Sorry, I don't understand that yet.\nThis feature will be available soon! Stay tuned! 😄🚀",
        show_alert=True,
    )
    await context.flush_metrics()
    metrics.assert_emitted(name=MetricKey.UNHANDLED_CALLBACK, value=1)


def test_bind_registers_fallback_through_metrics_wrapper():
    """The catch-all fallback must go through callback_with_metrics so unhandled interactions
    emit Fault/Time and get their buffered metrics flushed like every registered handler."""
    app = ApplicationBuilder().token("AAA").build()
    HandlersRegistry.bind(app)

    fallback_handler = app.handlers[0][-1]
    wrapped_callback = cast(Any, fallback_handler.callback)
    assert wrapped_callback is not callback_query_fallback
    assert wrapped_callback.__qualname__.startswith("callback_with_metrics")


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


async def test_all_handlers_emit_handler_metrics(app: StubMitupApp, update: Update, mock_session: MockDbSession):
    await app.initialize()

    # Define check_stat that is valid for conversation handlers
    check_state = [ConversationHandler.END, None, mock.AsyncMock(return_value=ConversationHandler.END), True]

    # Use a shared metrics client so we can aggregate all records across handler calls
    shared_client = make_test_metrics_client()
    valid_handlers = 0

    for wrapper in HandlersRegistry.handlers.values():
        # Ignore conversation handlers because those are never executed, only handlers registered in them
        if wrapper.is_conversation():
            continue

        valid_handlers += 1

        handler_context = build_context(update, app, metrics=shared_client)
        # Handlers that claim their update raise ApplicationHandlerStop after emitting
        # the same TIME/FAULT metrics, so absorbing it keeps the counts intact.
        with suppress(ApplicationHandlerStop):
            await wrapper.handler.handle_update(update, app, check_state, handler_context)

    # Every handler emits exactly one dimensionless TIME record and one dimensionless outcome record
    # (handler identity rides as an EMF property, so there is no separate per-handler-dimensioned
    # copy — issue #205). The outcome is FAULT, except handlers whose in-memory conversation state
    # was missing: the error handler reclassifies those to CONTEXT_LOST and the fault series stays
    # silent. FAULT and CONTEXT_LOST together account for every handler exactly once.
    metrics = MetricAssertions(shared_client)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=valid_handlers)
    outcome_names = {str(MetricKey.FAULT), str(MetricKey.CONTEXT_LOST)}
    outcome_records = [record for record in shared_client.records if record.name in outcome_names]
    assert len(outcome_records) == valid_handlers


# ---------------------------------------------------------------------------
# Error path of the metrics wrapper
# ---------------------------------------------------------------------------


async def test_handled_error_ends_the_conversation(
    update: Update, app: StubMitupApp, mock_session: MockDbSession, metrics_client: MetricsClient
):
    """A callback whose exception the wrapper handles reports END, so no conversation stays live
    behind the screen the error handler leaves the user on.

    `mock_session` backs the language lookup the error handler makes while building that screen.
    """

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="boom")
    async def command_boom(update: Update, context: StubMitupContext):
        raise RuntimeError("something went wrong mid-flow")

    result = await invoke(HandlerTestId.SOME_COMMAND, update, build_context(update, app, metrics=metrics_client))

    assert result == ConversationHandler.END
    ClearableRegistry.clear()


async def test_claimed_update_keeps_carrying_its_state(
    update: Update, app: StubMitupApp, metrics_client: MetricsClient
):
    """ApplicationHandlerStop is a success path: the wrapper re-raises it untouched so the state it
    carries still reaches PTB."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="claiming")
    async def command_claiming(update: Update, context: StubMitupContext):
        raise ApplicationHandlerStop(ConversationStates.STATE_ONE)

    with pytest.raises(ApplicationHandlerStop) as raised:
        await invoke(HandlerTestId.SOME_COMMAND, update, build_context(update, app, metrics=metrics_client))

    assert raised.value.state == ConversationStates.STATE_ONE
    ClearableRegistry.clear()


# ---------------------------------------------------------------------------
# Conversation handler registration (merged from test_conversation.py)
# ---------------------------------------------------------------------------


class CommandsTestId(HandlerId):
    COMMAND_REGISTERED = "new_command_registered"
    COMMAND_REGISTERED_2 = "other new command registered"
    COMMAND_NOT_REGISTERED = "not_registered"


class ConversationsTestId(HandlerId):
    CONVERSATION_WITH_NO_HANDLERS = "conversation_with_no_handlers"
    CONVERSATION_WITH_HANDLERS = "conversation_with_handlers"


def test_conversation_fails_without_existing_handler():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_REGISTERED)
    async def command_custom_new(update: Update, context: StubMitupContext):
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
    async def command_conversation_registered(update: Update, context: StubMitupContext):
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


# ---------------------------------------------------------------------------
# Admin-only gate (admin_only=True on register_command/message/callback_query)
# ---------------------------------------------------------------------------


def admin_context(update: Update, app: StubMitupApp) -> StubMitupContext:
    """A context whose acting user (DEFAULT_USER_ID) is on the admin allowlist."""
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])
    return build_context(update, app)


def non_admin_context(update: Update, app: StubMitupApp) -> StubMitupContext:
    """A context whose acting user is not on the (empty) admin allowlist."""
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([])
    return build_context(update, app)


async def invoke(handler_id: HandlerId, update: Update, context: StubMitupContext) -> object:
    """Run a ClearableRegistry handler's wrapped callback (metrics + gate + auto-answer)."""
    result = await ClearableRegistry.handlers[handler_id].handler.callback(update, context)
    await context.flush_metrics()
    return result


async def test_admin_only_command_runs_for_admin(update: Update, app: StubMitupApp):
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="admincmd", admin_only=True)
    async def command_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    result = await invoke(HandlerTestId.SOME_COMMAND, update, admin_context(update, app))

    called.assert_awaited_once()
    assert result == "RESULT"
    ClearableRegistry.clear()


async def test_admin_only_command_dropped_for_non_admin(update: Update, app: StubMitupApp):
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="admincmd", admin_only=True)
    async def command_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    result = await invoke(HandlerTestId.SOME_COMMAND, update, non_admin_context(update, app))

    called.assert_not_awaited()
    assert result is None
    ClearableRegistry.clear()


async def test_admin_only_message_runs_for_admin(update: Update, app: StubMitupApp):
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=TEXT, admin_only=True)
    async def message_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    await invoke(HandlerTestId.BINDABLE, update, admin_context(update, app))

    called.assert_awaited_once()
    ClearableRegistry.clear()


async def test_admin_only_message_dropped_for_non_admin(update: Update, app: StubMitupApp):
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=TEXT, admin_only=True)
    async def message_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    result = await invoke(HandlerTestId.BINDABLE, update, non_admin_context(update, app))

    called.assert_not_awaited()
    assert result is None
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_admin_only_callback_query_runs_for_admin(update: Update, app: StubMitupApp):
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, admin_only=True)
    async def callback_query_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    context = admin_context(update, app)
    await invoke(HandlerTestId.BINDABLE, update, context)

    called.assert_awaited_once()
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_admin_only_callback_query_dropped_for_non_admin_still_answers(update: Update, app: StubMitupApp):
    """A forged/stale callback from a non-admin is dropped, but the auto-answer still fires so the
    Telegram client spinner clears."""
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, admin_only=True)
    async def callback_query_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    context = non_admin_context(update, app)
    result = await invoke(HandlerTestId.BINDABLE, update, context)

    called.assert_not_awaited()
    assert result is None
    assert update.callback_query is not None
    context.bot.answer_callback_query.assert_awaited_once_with(update.callback_query.id)
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
async def test_admin_only_dropped_when_no_effective_user(update: Update, app: StubMitupApp):
    """No effective user means no admin identity, so the gate drops the update even when the
    allowlist is non-empty."""
    called = mock.AsyncMock(return_value="RESULT")

    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=TEXT, admin_only=True)
    async def message_admin(update: Update, context: StubMitupContext):
        return await called(update, context)

    # Allowlist is non-empty, but the update carries no effective user.
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])
    result = await invoke(HandlerTestId.BINDABLE, update, build_context(update, app))

    called.assert_not_awaited()
    assert result is None
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_post_commit_drain_failure_never_reaches_the_error_handler(
    update: Update,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    mock_session: MockDbSession,
    monkeypatch: pytest.MonkeyPatch,
):
    """The user's action committed before the queued calls ran, so a delivery that fails
    afterwards leaves them on the screen the handler rendered — the error handler, which would
    replace it with the generic error screen, is never reached."""
    monkeypatch.setattr("mitup_bot.api_wrapper.sleep", mock.AsyncMock())
    context = build_context(update, app, metrics=metrics_client)
    # The real api, not MockApi: the post-commit drain is what this exercises.
    context.api = cast(MockApi, build_api(context))
    edits = 0

    def edit_message_text(**kwargs: Any) -> object:
        nonlocal edits
        edits += 1
        # Only the user's own screen is reachable; the shared cards time out.
        if edits == 1:
            return mock.MagicMock()
        raise TimedOut()

    context.bot.edit_message_text.side_effect = edit_message_text

    @db.with_session(write=True)
    async def leave_meeting(session: AsyncSession, update: Update, context: StubMitupContext):
        await context.api.edit_message(update, "You left the meeting")
        await context.api.edit_message(update, "card shared in another chat")

    wrapped = callback_with_metrics(HandlerTestId.BINDABLE, "CallbackQuery", leave_meeting, Env.PROD)

    with mock.patch("mitup_bot.handlers.registry.error_handler") as error_handler:
        await wrapped(update, context)
    await context.flush_metrics()

    error_handler.assert_not_called()
    assert context.bot.edit_message_text.await_args_list[0].kwargs["text"] == "You left the meeting"
    # The interaction is counted as completed; the drain records its own failure separately.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0)
    metrics.assert_emitted(name=MetricKey.POST_COMMIT_API_FAULT)
