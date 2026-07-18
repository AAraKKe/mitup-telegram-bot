import pytest
from structlog.testing import capture_logs
from telegram import Update
from telegram.error import TelegramError

from mitup_bot.config import Env
from mitup_bot.custom_context import fault_fields_from_update
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    ContextPropertyNotSetError,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    GuardError,
    InactiveUserInteraction,
    InlineQueryNotSetError,
    MalformedCallbackData,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.handlers import error_handler
from mitup_bot.handlers.error_handler import SUPPRESSED_EXCEPTIONS
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.registry import callback_with_metrics
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import CommonMessages, PrivacyMessages
from mitup_bot.views import RenderContext, factory
from tests.helpers import (
    MockDbSession,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    build_context,
    create_settings,
    create_user,
)
from tests.helpers.fixtures import create_update
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize(
    "error, message",
    [(error, message) for error, messages in SUPPRESSED_EXCEPTIONS.items() for message in messages],
)
async def test_errors_ignored(error: type, message: str, context: StubMitupContext, metrics: MetricAssertions):
    error_obj = error(message)

    await error_handler.handler(context, error_obj, Env.DEV)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_not_found(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """When no user with that tg_user_id exists, handle_inactive_user returns silently without emitting metrics."""
    # Do not add any user to the session so the lookup returns None
    await error_handler.handle_inactive_user(context, tg_user_id=999)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_handle_inactive_user_error(
    context: StubMitupContext, user: User, mock_session: MockDbSession, metrics: MetricAssertions
):
    """MEMBER → LEFT transition: the metric MUST fire."""
    assert user.status is UserStatus.MEMBER

    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_joined_only_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """JOINED_ONLY users can never be DM-ed, so the error path must NOT transition them or emit the metric.

    Without this guard, every reminder send to a JOINED_ONLY user would mark them LEFT and
    eventually delete them via user_cleanup, defeating the entire purpose of the new enum.
    """
    user = create_user(id=10, tg_user_id=500, status=UserStatus.JOINED_ONLY)
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.JOINED_ONLY
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_left_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Re-hitting an already-LEFT user must not double-emit the INACTIVE_USER_SET metric."""
    user = create_user(id=11, tg_user_id=501, status=UserStatus.LEFT)
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


# --- Pending deletion handling ---


async def test_pending_deletion_answers_callback_query_with_alert(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A callback query from a marked user is answered with the alert and nothing else happens."""
    context = build_callback_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "es"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_answer_callback_query_called(
        context.telegram_update, text=PrivacyMessages.PENDING_DELETION_ALERT.get_text(lang="es"), show_alert=True
    )
    context.api.assert_send_message_not_called()
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_replies_to_message_updates(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A message update has no callback query to answer, so the alert is sent as a plain message."""
    context = build_message_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_send_message_called(
        context.telegram_update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang="en")
    )
    context.api.assert_method_just_called("answer_callback_query", times=0)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_answers_inline_queries_with_no_results(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """An inline query from a marked user is answered empty so the client spinner clears."""
    context = build_inline_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_answer_inline_query_called(context.telegram_update, results=[], cache_time=0)
    context.api.assert_send_message_not_called()
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_does_not_change_user_status(
    context: StubMitupContext, user: User, mock_session: MockDbSession
):
    """Unlike InactiveUserInteraction, the pending-deletion branch must not touch the user row:
    the DELETION_REQUESTED status is what the cleanup run keys on."""
    user.status = UserStatus.DELETION_REQUESTED
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, UserPendingDeletion(user.tg_user_id, "en"), Env.PROD)

    assert user.status is UserStatus.DELETION_REQUESTED


async def test_pending_deletion_suppresses_delivery_failures(app: StubMitupApp, mock_session: MockDbSession):
    """Delivery is best-effort: a failing answer must not escape as a second fault."""
    context = build_callback_context(app)
    context.api.mock_method("answer_callback_query").side_effect = TelegramError("answer failed")

    # Must not raise a second exception.
    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)


async def test_pending_deletion_returns_early_when_no_update(app: StubMitupApp, mock_session: MockDbSession):
    """When the context has no telegram update, the branch is a no-op (no send, no raise)."""
    context = build_message_context(app)
    # telegram_update is typed Update, but production reads it as Update | None and short-circuits on
    # None. Forcing None here is the only way to exercise that branch; it is an intentional test-only
    # violation, not a ty false positive, so it is exempted from requiring a tracking issue.
    context.telegram_update = None  # ty: ignore[invalid-assignment]  # nolink: intentional — exercising the None short-circuit branch

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)

    context.api.assert_method_just_called("send_message", times=0)
    context.api.assert_method_just_called("answer_callback_query", times=0)


async def test_handle_error_for_uncaght_exception(context: StubMitupContext, metrics: MetricAssertions):
    context.prepare_handler_metrics({"Handler": "SomeHandler", "HandlerType": "Callback"})

    try:
        # We need to raise the exception to have exec_info available when the error is handled
        raise RuntimeError()
    except RuntimeError:
        await error_handler.handler(context, RuntimeError(), Env.DEV)
        await context.metrics.flush()

    # The dimensionless aggregate FAULT is emitted exactly once (the infra alarms read it),
    # carrying the trigger context so the CloudWatch fault record is self-contained.
    metrics.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        times=1,
        properties={"UpdatePayload": fault_fields_from_update(context.telegram_update)},
    )
    # The prefixed fault is emitted once, carrying the handler identity as EMF properties.
    metrics.assert_emitted(
        name=MetricKey.FAULT.with_prefix("RuntimeError"),
        value=1,
        dimensions={},
        dimensions_exact=True,
        properties={"Handler": "SomeHandler", "HandlerType": "Callback"},
    )


async def test_handle_error_logs_the_exception(context: StubMitupContext):
    """Every fault produces a structured error line with the traceback attached, in every env —
    the log-side record that carries the handler contextvars the EMF Fault record lacks."""
    context.prepare_handler_metrics({"Handler": "SomeHandler", "HandlerType": "Callback"})
    error = ValueError("boom")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)

    fault_logs = [entry for entry in logs if entry["event"] == "An error occurred while handling the update"]
    assert len(fault_logs) == 1
    assert fault_logs[0]["log_level"] == "error"
    assert fault_logs[0]["exc_info"] is error
    # The failure log carries the trigger and its context so a fault is debuggable from the log
    # alone (what the user sent or pressed, and who/where).
    assert fault_logs[0]["update"] == fault_fields_from_update(context.telegram_update)
    assert "trigger_text" in fault_logs[0]["update"] or "callback_data" in fault_logs[0]["update"]


# --- Guard error handling ---

# Every guard exception must keep subclassing GuardError (so the error handler classifies it) and
# RuntimeError (so existing `except RuntimeError` behaviour is preserved). Reparenting these is the
# whole point of the feature; this guards against an accidental base change.
ALL_GUARD_ERRORS = [
    EffectiveUserNotSet,
    UserNotFound,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    CallbackQueryNotSet,
    MalformedCallbackData,
    InlineQueryNotSetError,
]


@pytest.mark.parametrize("guard_error", ALL_GUARD_ERRORS)
def test_guard_errors_subclass_guard_error_and_runtime_error(guard_error: type[Exception]):
    assert issubclass(guard_error, GuardError)
    assert issubclass(guard_error, RuntimeError)


def build_callback_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(callback_query=True))
    return build_context(update, app)


def build_message_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(message=True))
    return build_context(update, app)


def build_inline_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(message=False, inline_query="123"))
    return build_context(update, app)


async def test_guard_error_emits_fault_metrics_and_notifies_user(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A GuardError must emit both the prefixed and global FAULT metrics and redirect the user."""
    # No user registered -> resolve_lang falls back to the project default language.
    error = EffectiveMessageNotSet(context.telegram_update)

    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("EffectiveMessageNotSet"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_guard_error_uses_resolved_user_language(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """When the effective user exists in the DB, the notification uses that user's language."""
    # The default update fixture carries tg_user_id=123; register a matching user with a Spanish setting.
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    await error_handler.handler(context, EffectiveMessageNotSet(context.telegram_update), Env.PROD)
    await context.metrics.flush()

    expected_view = factory.main_menu_view(
        RenderContext(lang="es"), message=CommonMessages.UNEXPECTED_ERROR.get(lang="es")
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_non_guard_error_emits_fault_and_notifies(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A plain (non-guard) error still emits the full fault metrics AND redirects the user to the
    main menu with the generic notice, since any fault otherwise strands them mid-action."""
    # No user registered -> resolve_lang falls back to the project default language.
    await error_handler.handler(context, ValueError("boom"), Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("ValueError"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_fault_notification_failure_does_not_raise(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The post-fault user notification is best-effort: a delivery failure is swallowed, not re-raised
    as a second fault, while the original fault metrics still land."""
    context.api.mock_method("send_message").side_effect = TelegramError("send failed")

    # Must not raise despite the failing delivery.
    await error_handler.handler(context, ValueError("boom"), Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("ValueError"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)


# --- Context loss handling ---


async def test_context_lost_notifies_user_and_emits_dedicated_metric(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A lost-context error is an expected state, not a fault: the user is redirected to the main menu
    with the context-lost note, the dedicated CONTEXT_LOST metric fires, and no FAULT is emitted."""
    # No user registered -> resolve_lang falls back to the project default language.
    error = ContextPropertyNotSetError("User data 'meeting_id' requested but not set")

    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT.with_prefix("ContextPropertyNotSetError"), value=1)

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.CONTEXT_LOST.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_context_lost_uses_resolved_user_language(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The context-lost note renders in the effective user's language when they exist in the DB."""
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    await error_handler.handler(
        context, ContextPropertyNotSetError("User data 'meeting_id' requested but not set"), Env.PROD
    )
    await context.metrics.flush()

    expected_view = factory.main_menu_view(RenderContext(lang="es"), message=CommonMessages.CONTEXT_LOST.get(lang="es"))
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_context_lost_logs_at_warning_without_fault_line(context: StubMitupContext):
    """Context loss is logged as a warning and never produces the generic fault log line."""
    error = ContextPropertyNotSetError("User data 'meeting_id' requested but not set")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)

    event = "Conversation context was lost while handling the update"
    warning_logs = [entry for entry in logs if entry["event"] == event]
    assert len(warning_logs) == 1
    assert warning_logs[0]["log_level"] == "warning"
    assert warning_logs[0]["exc_info"] is error
    assert not [entry for entry in logs if entry["event"] == "An error occurred while handling the update"]


async def test_resolve_lang_returns_user_lang_when_user_exists(update: Update, mock_session: MockDbSession):
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    lang = await error_handler.resolve_lang(update)

    assert lang == "es"


async def test_resolve_lang_falls_back_when_update_is_none(mock_session: MockDbSession):
    lang = await error_handler.resolve_lang(None)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_resolve_lang_falls_back_when_no_effective_user(mock_session: MockDbSession):
    # An empty update has no effective_user.
    update = create_update(UpdateRequest(user=False, chat=False, message=False))

    lang = await error_handler.resolve_lang(update)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_resolve_lang_falls_back_when_user_not_in_db(update: Update, mock_session: MockDbSession):
    # Do not register any user so the DB lookup returns None.
    lang = await error_handler.resolve_lang(update)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_notify_guard_error_callback_query_answers_then_sends(app: StubMitupApp, mock_session: MockDbSession):
    """A callback-query update is acknowledged with an empty answer before the fresh message is sent."""
    context = build_callback_context(app)

    await error_handler.notify_guard_error(context)

    update = context.telegram_update
    # No user registered -> the notification falls back to the project default language.
    fallback = TranslationEngine.FALLBACK_LANG
    context.api.assert_answer_callback_query_called(update, text="", show_alert=False)
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(update, expected_view)


async def test_notify_guard_error_message_update_only_sends(app: StubMitupApp, mock_session: MockDbSession):
    """A plain message update has no callback query to answer, only the message is sent."""
    context = build_message_context(app)

    await error_handler.notify_guard_error(context)

    context.api.assert_method_just_called("answer_callback_query", times=0)
    context.api.assert_method_just_called("send_message", times=1)


async def test_notify_guard_error_returns_early_when_no_update(app: StubMitupApp, mock_session: MockDbSession):
    """When the context has no telegram update, notify_guard_error is a no-op (no send, no raise)."""
    context = build_message_context(app)
    # telegram_update is typed Update, but production reads it as Update | None and short-circuits on
    # None. Forcing None here is the only way to exercise that branch; it is an intentional test-only
    # violation, not a ty false positive, so it is exempted from requiring a tracking issue.
    context.telegram_update = None  # ty: ignore[invalid-assignment]  # nolink: intentional — exercising the None short-circuit branch

    await error_handler.notify_guard_error(context)

    context.api.assert_method_just_called("send_message", times=0)
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "raised",
    [
        TelegramError("send failed"),
        EffectiveChatNotSet(create_update(UpdateRequest(message=True))),
        RuntimeError("unexpected internal failure"),
    ],
)
async def test_notify_guard_error_suppresses_send_failures(
    app: StubMitupApp, mock_session: MockDbSession, raised: Exception
):
    """notify_guard_error swallows ALL exceptions during delivery, not just TelegramError/GuardError."""
    context = build_message_context(app)
    context.api.mock_method("send_message").side_effect = raised

    # Must not raise a second exception.
    await error_handler.notify_guard_error(context)


@pytest.mark.parametrize(
    "raised",
    [
        TelegramError("callback ack failed"),
        RuntimeError("unexpected internal failure in callback ack"),
    ],
)
async def test_notify_guard_error_suppresses_callback_ack_failures(
    app: StubMitupApp, mock_session: MockDbSession, raised: Exception
):
    """notify_guard_error swallows exceptions raised while acknowledging a callback query."""
    context = build_callback_context(app)
    context.api.mock_method("answer_callback_query").side_effect = raised

    # Must not raise a second exception.
    await error_handler.notify_guard_error(context)


async def test_successful_callback_emits_fault_zero_without_notification(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Regression: the real callback_with_metrics success path emits global FAULT=0 and never notifies.

    This drives the actual registry wrapper (not a hand-emitted metric) with a non-raising callback,
    proving the guard-error notification branch is only reached on failure.
    """
    handler_was_called = False

    async def successful_callback(update: Update, ctx: StubMitupContext):
        nonlocal handler_was_called
        handler_was_called = True

    wrapped = callback_with_metrics(MainMenuHandlerId.MAIN_MENU_CALLBACK, "Callback", successful_callback, Env.PROD)
    await wrapped(context.telegram_update, context)

    assert handler_was_called
    # callback_with_metrics emits a single dimensionless FAULT=0 on the success path.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
    # The guard-error branch is never reached, so no notification is sent.
    context.api.assert_send_message_not_called()
    context.api.assert_method_just_called("answer_callback_query", times=0)
