"""One line and one timing sample per outbound HTTP round-trip, shared by every outbound edge.

A record names the API method and nothing else derived from the request target: never a URL,
never a header. The Telegram Bot API is addressed as `https://api.telegram.org/bot<token>/<method>`,
so anything that records a request target publishes the bot token into a store that is readable
with view-only access and cannot be redacted.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter
from typing import Any

import structlog

from mitup_bot.monitoring.client import MetricsClient, current_metrics_client
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.monitoring.units import MetricUnit

log = structlog.get_logger(__name__)

# At or above this the peer failed; below it the peer answered. A 4xx is an answer — a rejected
# edit, a throttle, a blocked user — and belongs on the line's status_code rather than in the
# series an alarm watches.
SERVER_ERROR_STATUS = 500


class OutboundOutcome(StrEnum):
    OK = "ok"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    # The call raised something other than a timeout, so no readable response came back;
    # `error_type` on the line names the exact class.
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True, slots=True)
class OutboundEdge:
    """The names one outbound integration is recorded under.

    Instances are module constants so both metric names are literals fixed at author time: a name
    built from runtime data opens a separately billed CloudWatch series per distinct value.
    """

    event: str
    time_metric: str
    fault_metric: str


TELEGRAM_EDGE = OutboundEdge(
    event="Telegram API call",
    time_metric=MetricKey.TIME.with_prefix("TelegramApi", separator=""),
    fault_metric=MetricKey.FAULT.with_prefix("TelegramApi", separator=""),
)
PATREON_EDGE = OutboundEdge(
    event="Patreon API call",
    time_metric=MetricKey.TIME.with_prefix("PatreonApi", separator=""),
    fault_metric=MetricKey.FAULT.with_prefix("PatreonApi", separator=""),
)
GOOGLE_EDGE = OutboundEdge(
    event="Google API call",
    time_metric=MetricKey.TIME.with_prefix("GoogleApi", separator=""),
    fault_metric=MetricKey.FAULT.with_prefix("GoogleApi", separator=""),
)


@dataclass
class OutboundResult:
    """What a timed block learned about its round-trip: the status code, when its client exposes
    one, and any extra fields the block resolved for the line."""

    status_code: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)


def qualified_type(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def outcome_for_status(status_code: int | None) -> OutboundOutcome:
    """A client that exposes no status code (the Google SDK raises instead) reports `ok` on a
    clean return."""
    if status_code is None or 200 <= status_code < 300:
        return OutboundOutcome.OK
    return OutboundOutcome.HTTP_ERROR


def is_fault(outcome: OutboundOutcome, status_code: int | None) -> bool:
    if outcome is OutboundOutcome.TIMEOUT or outcome is OutboundOutcome.NETWORK_ERROR:
        return True
    return status_code is not None and status_code >= SERVER_ERROR_STATUS


def record_call(
    edge: OutboundEdge,
    api_method: str,
    outcome: OutboundOutcome,
    duration_ms: float,
    *,
    client: MetricsClient | None = None,
    log_call: bool = True,
    status_code: int | None = None,
    error_type: str | None = None,
    **fields: Any,
):
    """Write the edge's timing and fault samples, and unless the caller switched it off, the line.

    Without an explicit `client` the samples ride on whichever one the invocation on the stack
    published, so they join that flush window and inherit its correlation key (`update_id` in the
    bot, `run_id` in the events runner) instead of carrying properties of their own. Outside any
    invocation there is no client and only the line is written. `log_call` never gates the metrics:
    a series with holes in it cannot be alarmed on.
    """
    resolved = client or current_metrics_client()
    if resolved is not None:
        resolved.emit(edge.time_metric, duration_ms, MetricUnit.MILLISECONDS)
        resolved.emit(edge.fault_metric, 1 if is_fault(outcome, status_code) else 0)

    if not log_call:
        return

    line: dict[str, Any] = {"api_method": api_method, "duration_ms": round(duration_ms, 1), "outcome": str(outcome)}
    if status_code is not None:
        line["status_code"] = status_code
    if error_type is not None:
        line["error_type"] = error_type
    log.info(edge.event, **(line | fields))


@contextmanager
def outbound_call(
    edge: OutboundEdge,
    api_method: str,
    *,
    timeout_errors: tuple[type[BaseException], ...] = (),
    client: MetricsClient | None = None,
    log_call: bool = True,
    **fields: Any,
) -> Generator[OutboundResult]:
    """Time the block and record exactly one round-trip for it, however it exits.

    The block sets `status_code` on the yielded result when its client exposes one, and may add
    fields resolved from the response. Classifying a timeout stays with the caller — each client
    library has its own hierarchy for it — so the exception types that mean "timed out" are passed
    in rather than guessed at here.
    """
    result = OutboundResult()
    start = perf_counter()
    try:
        yield result
    except Exception as error:
        outcome = OutboundOutcome.TIMEOUT if isinstance(error, timeout_errors) else OutboundOutcome.NETWORK_ERROR
        record_call(
            edge,
            api_method,
            outcome,
            1000 * (perf_counter() - start),
            client=client,
            log_call=log_call,
            error_type=qualified_type(error),
            **(fields | result.fields),
        )
        raise

    record_call(
        edge,
        api_method,
        outcome_for_status(result.status_code),
        1000 * (perf_counter() - start),
        client=client,
        log_call=log_call,
        status_code=result.status_code,
        **(fields | result.fields),
    )
