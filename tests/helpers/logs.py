from structlog.typing import EventDict

from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import ContextPropertyNotSetError

# The classification a recovered conversation-context loss must carry, spelled out here rather than
# imported from the handlers so a reworded event name, reason or level fails the assertion instead
# of silently travelling into it.
CONTEXT_LOST_LOG_EVENT = "Conversation context was lost while handling the update"
CONTEXT_LOST_LOG_REASON = "conversation_context_missing"


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
