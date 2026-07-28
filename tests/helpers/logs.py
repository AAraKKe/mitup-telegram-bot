import gc
import logging

import pytest
from structlog._config import BoundLoggerLazyProxy
from structlog.typing import EventDict

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import ContextPropertyNotSetError

# The classification a recovered conversation-context loss must carry, spelled out here rather than
# imported from the handlers so a reworded event name, reason or level fails the assertion instead
# of silently travelling into it.
CONTEXT_LOST_LOG_EVENT = "Conversation context was lost while handling the update"
CONTEXT_LOST_LOG_REASON = "conversation_context_missing"

# Likewise for the one event every meeting rejection is recorded under.
MEETING_REJECTION_LOG_EVENT = "Rejected meeting action"


def assert_meeting_rejection_logged(caplog: pytest.LogCaptureFixture, action: str, reason: str):
    """Assert the guard's rejection reached the log naming what the caller was attempting.

    The fields ride as `LogRecord` attributes (`render_to_log_kwargs` in the suite's structlog
    pipeline), so they are read off the record rather than out of `caplog.text`.
    """
    rejections = [record for record in caplog.records if record.msg == MEETING_REJECTION_LOG_EVENT]
    assert len(rejections) == 1, f"expected one rejection line, captured {[record.msg for record in caplog.records]}"

    # The fields are injected onto the record via `extra`, so they are not declared attributes.
    assert rejections[0].levelname == "WARNING"
    assert getattr(rejections[0], "reason", None) == reason
    assert getattr(rejections[0], "action", None) == action


def drop_cached_logger_binds():
    """Reset every lazy structlog proxy so it re-binds against whatever pipeline is configured next.

    The production `configure_logging` sets `cache_logger_on_first_use=True`, so the first line a
    module-level `structlog.get_logger(__name__)` emits under it freezes that proxy onto the
    `wrap_for_formatter` pipeline. That cache outlives a later `structlog.configure`, so a test
    reusing the same module's logger would capture nothing and would ship a live, non-serializable
    logger object across xdist's channel. Call this on teardown of any test that runs the production
    logging setup.
    """
    for proxy in (obj for obj in gc.get_objects() if isinstance(obj, BoundLoggerLazyProxy)):
        proxy.__dict__.pop("bind", None)


def log_record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    """The captured record for `event`, whose structlog fields ride as record attributes.

    Capture also picks up unrelated framework lines, so the lookup filters by the structlog event
    string (the LogRecord message) rather than taking whatever was logged last. The fields are not
    part of `caplog.text`, so asserting on a field means reading it off the record.
    """
    return next(record for record in caplog.records if record.message == event)


def assert_context_lost_logged(logs: list[EventDict], context_id: ContextId):
    """Assert the captured lines classify exactly one context loss, and none of them as a fault.

    *logs* comes from ``structlog.testing.capture_logs`` wrapped around the handler call.
    """
    losses = [entry for entry in logs if entry["event"] == CONTEXT_LOST_LOG_EVENT]
    assert len(losses) == 1, f"expected one context-loss line, captured {[entry['event'] for entry in logs]}"

    loss = losses[0]
    assert loss["log_level"] == "warning"
    assert loss["reason"] == CONTEXT_LOST_LOG_REASON
    assert loss["context_id"] == context_id.value
    assert isinstance(loss["exc_info"], ContextPropertyNotSetError)

    faults = [entry for entry in logs if entry["log_level"] in {"error", "critical"}]
    assert not faults, f"a recovered context loss must not be logged as a fault, got {faults}"
