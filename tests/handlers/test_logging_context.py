import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram import Update

from mitup_bot.config import Env
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.registry import callback_with_metrics, handler_log_context
from tests.helpers import StubMitupApp, StubMitupContext, build_context

# The default `update`/`tg_*` fixtures resolve effective_user.id, effective_chat.id and
# update_id all to 123 (DEFAULT_USER_ID / DEFAULT_CHAT_ID / DEFAULT_MESSAGE_ID).
DEFAULT_ID = 123


class LoggingTestId(HandlerId):
    SOME_HANDLER = "logging_test_handler"


def test_handler_log_context_includes_request_fields(update: Update):
    """handler_log_context binds handler/handler_type/update_id plus user_id/chat_id when present."""
    fields = handler_log_context(LoggingTestId.SOME_HANDLER, "Command", update)

    assert fields == {
        "handler": LoggingTestId.SOME_HANDLER.dimension,
        "handler_type": "Command",
        "update_id": DEFAULT_ID,
        "user_id": DEFAULT_ID,
        "chat_id": DEFAULT_ID,
    }


def test_handler_log_context_omits_missing_user_and_chat():
    """Updates without an effective_user/chat (e.g. a bare poll update) omit those keys rather
    than binding None."""
    update = Update(update_id=999)

    fields = handler_log_context(LoggingTestId.SOME_HANDLER, "Callback", update)

    assert fields == {
        "handler": LoggingTestId.SOME_HANDLER.dimension,
        "handler_type": "Callback",
        "update_id": 999,
    }
    assert "user_id" not in fields
    assert "chat_id" not in fields


async def test_log_inside_callback_carries_request_contextvars(update: Update, app: StubMitupApp, mock_session: object):
    """A log emitted from inside a callback wrapped by callback_with_metrics carries the
    handler/handler_type/user_id/chat_id/update_id fields bound for the request."""
    context = build_context(update, app)

    async def callback(_update: Update, _context: StubMitupContext):
        structlog.get_logger("mitup_bot").info("inside handler")

    wrapped = callback_with_metrics(LoggingTestId.SOME_HANDLER, "Command", callback, Env.PROD)

    # merge_contextvars must run inside capture_logs so the bound contextvars land on the event;
    # capture_logs otherwise disables the configured processor chain (including merge_contextvars).
    with capture_logs(processors=[merge_contextvars]) as logs:
        await wrapped(update, context)

    handler_logs = [log for log in logs if log["event"] == "inside handler"]
    assert len(handler_logs) == 1
    entry = handler_logs[0]
    assert entry["handler"] == LoggingTestId.SOME_HANDLER.dimension
    assert entry["handler_type"] == "Command"
    assert entry["user_id"] == DEFAULT_ID
    assert entry["chat_id"] == DEFAULT_ID
    assert entry["update_id"] == DEFAULT_ID


async def test_request_contextvars_cleared_after_callback_returns(
    update: Update, app: StubMitupApp, mock_session: object
):
    """bound_contextvars auto-clears on exit, so request fields must not leak into a log emitted
    after the wrapped callback returns (PTB reuses the worker task across updates)."""
    context = build_context(update, app)

    async def callback(_update: Update, _context: StubMitupContext):
        return None

    wrapped = callback_with_metrics(LoggingTestId.SOME_HANDLER, "Command", callback, Env.PROD)

    with capture_logs(processors=[merge_contextvars]) as logs:
        await wrapped(update, context)
        # Emitted outside the handler, after it returned — must carry none of the request fields.
        structlog.get_logger("mitup_bot").info("after handler")

    after_logs = [log for log in logs if log["event"] == "after handler"]
    assert len(after_logs) == 1
    entry = after_logs[0]
    for field in ("handler", "handler_type", "user_id", "chat_id", "update_id"):
        assert field not in entry


def test_context_log_returns_usable_logger(update: Update, app: StubMitupApp):
    """MitupContext.log exposes a structlog logger so handlers can log via context.log.info(...).

    get_logger returns a lazy proxy that materializes into the configured wrapper_class
    (structlog.stdlib.BoundLogger in production) on first use, so assert on behavior — that the
    logger actually emits — rather than the concrete proxy type.
    """
    context = build_context(update, app)

    with capture_logs() as logs:
        context.log.info("from context.log", custom_field="value")

    matches = [log for log in logs if log["event"] == "from context.log"]
    assert len(matches) == 1
    assert matches[0]["custom_field"] == "value"
