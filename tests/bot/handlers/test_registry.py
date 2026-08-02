from contextlib import suppress
from enum import Enum, auto
from typing import Any, cast
from unittest import mock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop, CommandHandler, ConversationHandler
from telegram.ext.filters import PHOTO, TEXT, BaseFilter

from mitup_bot import db
from mitup_bot.api_wrapper import build_api
from mitup_bot.callback_data import CallbackData
from mitup_bot.config import Env
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.exceptions import (
    ContextPropertyNotSetError,
    HandlerNotRegistered,
    HandlerRegisteredError,
    UnboundCallbackError,
)
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState
from mitup_bot.handlers.registry import (
    HandlerWrapper,
    UnhandledHandlerId,
    callback_query_fallback,
    callback_with_metrics,
    process_update_error,
)
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.update_trace import UPDATE_TRACE, UpdateTrace
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages
from mitup_bot.views import RenderContext, factory
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


def test_bind_reports_what_it_bound_rather_than_one_line_per_handler():
    """~90 near-identical per-handler lines bury the rest of startup, and none of them is what an
    operator checks: a missing registration shows up in the aggregate."""
    app = ApplicationBuilder().token("AAA").build()

    with capture_logs() as logs:
        HandlersRegistry.bind(app)

    assert all(entry["log_level"] == "debug" for entry in logs if entry["event"] == "Binding handler to application")
    summary = next(entry for entry in logs if entry["event"] == "Bound handlers to the application")
    bound_handlers = [handler for handler_list in app.handlers.values() for handler in handler_list]
    # The fallback is appended after the summary, so the count is one short of what the app carries.
    assert summary["handlers_bound"] == len(bound_handlers) - 1
    assert summary["conversation_handlers"] == sum(
        1 for handler in bound_handlers if isinstance(handler, ConversationHandler)
    )
    assert summary["groups"] == sorted(app.handlers)


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


UNBOUND_CALLBACK = CallbackData(action="ghost", entity="button")


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=UNBOUND_CALLBACK.with_id(3))], indirect=True)
async def test_an_unmatched_callback_names_the_data_that_matched_nothing(
    update: Update,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """The data is the only evidence of which button — or which forged payload — reached the
    fallback, and the Bot API bounds it to 64 bytes, so the exception message carries it."""
    context = build_context(update, app, metrics=metrics_client)
    assert update.callback_query is not None

    with pytest.raises(UnboundCallbackError) as raised:
        await callback_query_fallback(update, context)

    assert str(raised.value) == f"Callback data {update.callback_query.data!r} matched no registered handler"
    # The fallback speaks to Telegram not at all; whatever the user sees is the error handler's.
    context.bot.answer_callback_query.assert_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=UNBOUND_CALLBACK.with_id(3))], indirect=True)
async def test_an_unmatched_callback_closes_the_invocation_as_a_fault(
    update: Update,
    app: StubMitupApp,
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """Every shipped button has a handler, so an unmatched callback is a defect of ours or a forged
    payload — not a feature the user should be told to wait for. Driven through the wrapper it is
    indistinguishable from any other fault: one Fault=1 sample naming the class, the correlated
    error line, and the generic redirect.

    `mock_session` backs the language lookup the error handler makes while building that redirect.
    """
    context = build_context(update, app, metrics=metrics_client)
    wrapped = callback_with_metrics(
        UnhandledHandlerId.CALLBACK_QUERY_FALLBACK, "Callback", callback_query_fallback, Env.PROD
    )

    with capture_logs(processors=[merge_contextvars]) as logs:
        await wrapped(update, context)
    await context.flush_metrics()

    metrics.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        times=1,
        properties={"error_type": "mitup_bot.exceptions.UnboundCallbackError"},
    )

    faults = [entry for entry in logs if entry["event"] == "An error occurred while handling the update"]
    assert len(faults) == 1
    assert faults[0]["log_level"] == "error"
    assert faults[0]["error_type"] == "mitup_bot.exceptions.UnboundCallbackError"
    assert faults[0]["update_id"] == update.update_id
    assert isinstance(faults[0]["exc_info"], UnboundCallbackError)

    fallback_lang = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback_lang), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback_lang)
    )
    context.api.assert_send_message_called(update, expected_view)


def test_bind_registers_fallback_through_metrics_wrapper():
    """The catch-all fallback must go through callback_with_metrics so unmatched interactions
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

    # Every handler emits exactly one dimensionless TIME record and exactly one dimensionless FAULT
    # record (handler identity rides as an EMF property, so there is no separate
    # per-handler-dimensioned copy — issue #205). The FAULT sample lands on every exit path,
    # including the handlers whose in-memory conversation state was missing: those are reclassified
    # to CONTEXT_LOST for alarming but still close the invocation on FAULT=0.
    metrics = MetricAssertions(shared_client)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=valid_handlers)
    metrics.assert_emitted(name=MetricKey.FAULT, times=valid_handlers)


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
    update: Update, app: StubMitupApp, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """ApplicationHandlerStop is a success path: the wrapper re-raises it untouched so the state it
    carries still reaches PTB, and the invocation still closes on its one FAULT=0 sample."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="claiming")
    async def command_claiming(update: Update, context: StubMitupContext):
        raise ApplicationHandlerStop(ConversationStates.STATE_ONE)

    with pytest.raises(ApplicationHandlerStop) as raised:
        await invoke(HandlerTestId.SOME_COMMAND, update, build_context(update, app, metrics=metrics_client))

    assert raised.value.state == ConversationStates.STATE_ONE
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    ClearableRegistry.clear()


# The three shapes the wrapper has to tell apart. Which value each individual exception maps to is
# the error handler's business and is asserted per branch in test_error_handler.py; here the
# emission is one shared line, so a fourth exception would exercise identical code. `ty` rejects a
# branch that exits without an outcome, so a silently-added exit cannot regress past the checker.
FAULT_CLASSIFICATIONS = [
    pytest.param(RuntimeError("boom"), 1, False, id="genuine_fault"),
    pytest.param(
        ContextPropertyNotSetError("User data 'meeting_id' requested but not set"), 0, False, id="handled_error"
    ),
    pytest.param(RuntimeError("boom"), 1, True, id="error_handler_itself_raises"),
]


@pytest.mark.parametrize("error, expected_fault, error_handler_raises", FAULT_CLASSIFICATIONS)
async def test_every_classified_exit_lands_exactly_one_fault_sample(
    update: Update,
    app: StubMitupApp,
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    error: Exception,
    expected_fault: float,
    error_handler_raises: bool,
):
    """The fault-rate alarm reads FAULT's SampleCount as its request denominator, so an invocation
    the error handler answers without a fault must still land a 0 sample rather than nothing. A
    rolling deploy produces a burst of exactly this context loss by design, at the moment the ECS
    rollback bake is reading that alarm. An error handler that breaks mid-answer is the one case
    where the wrapper has to classify the fault itself."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="classify")
    async def command_classify(update: Update, context: StubMitupContext):
        raise error

    context = build_context(update, app, metrics=metrics_client)
    with capture_logs(processors=[merge_contextvars]) as logs:
        if error_handler_raises:
            broken = mock.patch(
                "mitup_bot.handlers.registry.error_handler",
                new_callable=mock.AsyncMock,
                side_effect=ValueError("the error handler blew up"),
            )
            # The error handler's own exception still leaves for process_update, unhandled.
            with broken, pytest.raises(ValueError, match="the error handler blew up"):
                await invoke(HandlerTestId.SOME_COMMAND, update, context)
        else:
            await invoke(HandlerTestId.SOME_COMMAND, update, context)

    metrics.assert_emitted(name=MetricKey.FAULT, value=expected_fault, times=1)
    if expected_fault:
        # Both fault shapes name the exception the *callback* raised. An error handler that broke
        # while answering it does not rename the fault after itself.
        metrics.assert_emitted(name=MetricKey.FAULT, value=1, properties={"error_type": "builtins.RuntimeError"})

    if error_handler_raises:
        # The escaping exception reaches PTB only after the contextvars have unwound, so this line
        # is the sole record of the failure that can be joined to the invocation it broke.
        failures = [entry for entry in logs if entry["event"] == "Error handler failed while answering a fault"]
        assert len(failures) == 1
        entry = failures[0]
        assert entry["log_level"] == "error"
        assert entry["exc_info"] is True
        assert entry["original_error_type"] == "builtins.RuntimeError"
        assert entry["handler"] == HandlerTestId.SOME_COMMAND.dimension
        assert entry["update_id"] == update.update_id
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


# ---------------------------------------------------------------------------
# The handler entry/exit pair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(7))], indirect=True)
async def test_the_entry_line_names_what_the_user_pressed(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """What the user tapped exists nowhere in the log today; the entry line is where it lands."""

    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, auto_answer=False)
    async def callback_query_probe(update: Update, context: StubMitupContext): ...

    assert update.callback_query is not None
    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.BINDABLE, update, context)

    entries = [entry for entry in logs if entry["event"] == "Entering handler"]
    assert len(entries) == 1
    assert entries[0]["log_level"] == "debug"
    assert entries[0]["callback_data"] == update.callback_query.data
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(7))], indirect=True)
async def test_the_exit_line_names_the_button_that_was_pressed(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """Prod runs INFO, where the entry line does not exist — so the exit line is the only record of
    which button produced the interaction."""

    @ClearableRegistry.register_callback_query(HandlerTestId.BINDABLE, auto_answer=False)
    async def callback_query_probe(update: Update, context: StubMitupContext): ...

    assert update.callback_query is not None
    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.BINDABLE, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert len(exits) == 1
    assert exits[0]["log_level"] == "info"
    assert exits[0]["callback_data"] == update.callback_query.data
    assert "command" not in exits[0]
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(command="whoami", command_args="a-deep-link-payload")], indirect=True)
async def test_the_exit_line_names_the_command_without_its_arguments(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """The command is a bounded value, but what follows it is free user text — a deep-link payload,
    a search term — that no log line may carry."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="whoami")
    async def command_whoami(update: Update, context: StubMitupContext): ...

    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.SOME_COMMAND, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert len(exits) == 1
    assert exits[0]["command"] == "/whoami"
    assert "a-deep-link-payload" not in str(exits[0])
    assert "callback_data" not in exits[0]
    ClearableRegistry.clear()


@pytest.mark.parametrize("update", [UpdateRequest(message_text="what the user typed")], indirect=True)
async def test_the_exit_line_names_no_input_for_a_plain_message(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """A message that is not a command is free user text end to end, so nothing about what was typed
    reaches the line."""

    @ClearableRegistry.register_message(HandlerTestId.BINDABLE, filters=TEXT)
    async def typed_message_handler(update: Update, context: StubMitupContext): ...

    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.BINDABLE, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert len(exits) == 1
    assert "callback_data" not in exits[0]
    assert "command" not in exits[0]
    assert "what the user typed" not in str(exits[0])
    ClearableRegistry.clear()


HANDLER_EXITS: list[tuple[object, str, str]] = [
    (ConversationSettingsState.TIMEZONE, "success", "TIMEZONE"),
    (None, "success", "none"),
    (ConversationHandler.END, "success", "end"),
]


@pytest.mark.parametrize("return_value, outcome, result", HANDLER_EXITS, ids=["state", "no-state", "end"])
async def test_the_exit_line_reports_the_conversation_transition(
    return_value: object,
    outcome: str,
    result: str,
    update: Update,
    app: StubMitupApp,
    mock_session: MockDbSession,
):
    """PTB reads the return value as the next conversation state and the wrapper then drops it —
    this is the only record that the user moved (or did not) between steps."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="transition")
    async def command_transition(update: Update, context: StubMitupContext):
        return return_value

    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.SOME_COMMAND, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert len(exits) == 1
    assert exits[0]["log_level"] == "info"
    assert exits[0]["outcome"] == outcome
    assert exits[0]["result"] == result
    ClearableRegistry.clear()


async def test_a_claimed_update_is_reported_as_a_handler_stop(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """`ApplicationHandlerStop` stops every remaining handler group and has never been visible."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="claiming")
    async def command_claiming(update: Update, context: StubMitupContext):
        raise ApplicationHandlerStop(ConversationSettingsState.TIMEZONE)

    context = build_context(update, app)
    with capture_logs() as logs, suppress(ApplicationHandlerStop):
        await invoke(HandlerTestId.SOME_COMMAND, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert [(entry["outcome"], entry["result"]) for entry in exits] == [("handler_stop", "TIMEZONE")]
    ClearableRegistry.clear()


async def test_a_handled_error_is_reported_as_an_error_exit(
    update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """The error handler ends the flow, so the exit line pairs `error` with the END it forced."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="failing")
    async def command_failing(update: Update, context: StubMitupContext):
        raise RuntimeError("boom")

    context = build_context(update, app)
    with capture_logs() as logs:
        await invoke(HandlerTestId.SOME_COMMAND, update, context)

    exits = [entry for entry in logs if entry["event"] == "Leaving handler"]
    assert [(entry["outcome"], entry["result"]) for entry in exits] == [("error", "end")]
    ClearableRegistry.clear()


@pytest.mark.parametrize("faulted", [False, True], ids=["clean", "faulted"])
async def test_the_invocation_reports_itself_to_the_update_trace(
    faulted: bool, update: Update, app: StubMitupApp, mock_session: MockDbSession
):
    """The processor writes the update's exit line from outside this wrapper and PTB tells it
    nothing, so `handled` vs `unrouted` vs `failed` rests on this report."""

    @ClearableRegistry.register_command(HandlerTestId.SOME_COMMAND, command="reporting")
    async def command_reporting(update: Update, context: StubMitupContext):
        if faulted:
            raise RuntimeError("boom")

    trace = UpdateTrace()
    token = UPDATE_TRACE.set(trace)
    try:
        await invoke(HandlerTestId.SOME_COMMAND, update, build_context(update, app))
    finally:
        UPDATE_TRACE.reset(token)

    assert trace.handlers_run == 1
    assert trace.fault_recorded is faulted
    ClearableRegistry.clear()


# ---------------------------------------------------------------------------
# The PTB error handler
# ---------------------------------------------------------------------------


def test_bind_registers_the_ptb_error_handler():
    """Without it PTB logs whatever escapes a callback itself, uncorrelated and unmeasured."""
    app = ApplicationBuilder().token("AAA").build()

    HandlersRegistry.bind(app)

    assert process_update_error in app.error_handlers


async def test_an_error_outside_any_handler_is_correlated_and_counted(
    update: Update, app: StubMitupApp, metrics_client: MetricsClient, metrics: MetricAssertions
):
    context = build_context(update, app, metrics=metrics_client)
    context.error = RuntimeError("nothing owned this")

    with capture_logs(processors=[merge_contextvars]) as logs:
        await process_update_error(update, context)

    lines = [entry for entry in logs if entry["event"] == "An update failed outside its handler"]
    assert len(lines) == 1
    assert lines[0]["update_id"] == update.update_id
    assert lines[0]["error_type"] == "builtins.RuntimeError"
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)


async def test_an_error_a_wrapped_invocation_already_recorded_is_left_alone(
    update: Update, app: StubMitupApp, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """The invocation owns a failure that passed through it, on both planes: the registry writes
    the line and `Fault` is exactly-once per invocation. Repeating either here would double-log
    every re-raise and halve the value the fault-rate alarm reads on twice the denominator."""
    context = build_context(update, app, metrics=metrics_client)
    context.error = RuntimeError("the error handler blew up while answering")

    token = UPDATE_TRACE.set(UpdateTrace(handlers_run=1, fault_recorded=True))
    try:
        with capture_logs() as logs:
            await process_update_error(update, context)
    finally:
        UPDATE_TRACE.reset(token)
    await context.flush_metrics()

    assert logs == []
    metrics.assert_not_emitted(name=MetricKey.FAULT)
