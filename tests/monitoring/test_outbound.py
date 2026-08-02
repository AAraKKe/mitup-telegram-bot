import asyncio

import pytest
from structlog.testing import capture_logs

from mitup_bot.monitoring import MetricsClient, MetricUnit, bound_metrics_client, current_metrics_client
from mitup_bot.monitoring.outbound import (
    GOOGLE_EDGE,
    PATREON_EDGE,
    TELEGRAM_EDGE,
    OutboundEdge,
    OutboundOutcome,
    outbound_call,
)
from tests.helpers import AnyFloat
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


class StubTimeout(RuntimeError): ...


@pytest.fixture
def client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(client)


async def test_a_completed_round_trip_lands_one_line_and_one_timing_pair(
    client: MetricsClient, metrics: MetricAssertions
):
    with capture_logs() as logs:
        with outbound_call(TELEGRAM_EDGE, "sendMessage", client=client, chat_id=42) as call:
            call.status_code = 200

    await client.flush()

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name="TelegramApiFault", value=0, times=1)

    (line,) = [entry for entry in logs if entry["event"] == "Telegram API call"]
    assert line["api_method"] == "sendMessage"
    assert line["outcome"] == OutboundOutcome.OK
    assert line["status_code"] == 200
    assert line["chat_id"] == 42
    assert isinstance(line["duration_ms"], float)


@pytest.mark.parametrize("edge", [PATREON_EDGE, GOOGLE_EDGE], ids=["patreon", "google"])
async def test_an_edge_without_a_time_metric_records_its_duration_only_on_the_line(
    edge: OutboundEdge, client: MetricsClient, metrics: MetricAssertions
):
    """Only the Telegram edge charts latency as a series; the rest answer `how slow` from the line.

    A latency series nothing reads still bills per series-month, while `duration_ms` on the line
    answers the same question through `stats avg(duration_ms), pct(duration_ms, 99) by api_method`.
    """
    assert edge.time_metric is None

    with capture_logs() as logs:
        with outbound_call(edge, "identity", client=client) as call:
            call.status_code = 200

    await client.flush()

    assert [record.name for record in client.records] == [edge.fault_metric]
    metrics.assert_emitted(name=edge.fault_metric, value=0, times=1)
    (line,) = [entry for entry in logs if entry["event"] == edge.event]
    assert isinstance(line["duration_ms"], float)


@pytest.mark.parametrize(
    ("status_code", "expected_fault"),
    [(429, 0), (400, 0), (503, 1)],
    ids=["throttled", "rejected", "peer_failed"],
)
async def test_only_a_server_error_counts_on_the_fault_series(
    client: MetricsClient, metrics: MetricAssertions, status_code: int, expected_fault: int
):
    # A 4xx is the peer answering us — a throttle, a rejected edit — and belongs on the line's
    # status_code, not in the series an alarm watches.
    with capture_logs() as logs:
        with outbound_call(TELEGRAM_EDGE, "editMessageText", client=client) as call:
            call.status_code = status_code

    await client.flush()

    metrics.assert_emitted(name="TelegramApiFault", value=expected_fault, times=1)
    (line,) = [entry for entry in logs if entry["event"] == "Telegram API call"]
    assert line["outcome"] == OutboundOutcome.HTTP_ERROR


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        (StubTimeout("stalled"), OutboundOutcome.TIMEOUT),
        (ConnectionError("refused"), OutboundOutcome.NETWORK_ERROR),
    ],
    ids=["timeout", "other_error"],
)
async def test_a_raising_round_trip_is_recorded_and_re_raised(
    client: MetricsClient, metrics: MetricAssertions, error: Exception, expected_outcome: OutboundOutcome
):
    with capture_logs() as logs:
        with pytest.raises(type(error)):
            with outbound_call(TELEGRAM_EDGE, "sendMessage", timeout_errors=(StubTimeout,), client=client):
                raise error

    await client.flush()

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name="TelegramApiFault", value=1, times=1)

    (line,) = [entry for entry in logs if entry["event"] == "Telegram API call"]
    assert line["outcome"] == expected_outcome
    assert line["error_type"] == f"{type(error).__module__}.{type(error).__qualname__}"
    assert "status_code" not in line


async def test_switching_the_line_off_keeps_the_series_continuous(client: MetricsClient, metrics: MetricAssertions):
    with capture_logs() as logs:
        with outbound_call(TELEGRAM_EDGE, "sendMessage", client=client, log_call=False) as call:
            call.status_code = 200

    await client.flush()

    assert logs == []
    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name="TelegramApiFault", value=0, times=1)


async def test_without_an_explicit_client_the_samples_join_the_ambient_flush_window(
    client: MetricsClient, metrics: MetricAssertions
):
    with bound_metrics_client(client):
        with outbound_call(TELEGRAM_EDGE, "sendMessage") as call:
            call.status_code = 200

    await client.flush()

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    assert current_metrics_client() is None


async def test_calls_fanned_out_into_tasks_reach_the_same_flush_window(
    client: MetricsClient, metrics: MetricAssertions
):
    """A fan-out gathers its deliveries into tasks. Each one copies the context at creation, so it
    carries the binding that was live when the fan-out started — the samples do not scatter."""

    async def deliver():
        with outbound_call(TELEGRAM_EDGE, "sendMessage", log_call=False) as call:
            call.status_code = 200

    with bound_metrics_client(client):
        await asyncio.gather(deliver(), deliver(), deliver())

    await client.flush()

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=3)
    metrics.assert_emitted(name="TelegramApiFault", value=0, times=3)


async def test_a_call_after_the_binding_ends_does_not_reach_the_flushed_client(
    client: MetricsClient, metrics: MetricAssertions
):
    """The binding is restored on exit, so a later call on the same task — PTB reuses its workers,
    and its own getMe/setWebhook run outside every invocation — cannot be attributed to an
    invocation that has already flushed."""
    with bound_metrics_client(client):
        with outbound_call(TELEGRAM_EDGE, "sendMessage", log_call=False) as call:
            call.status_code = 200

    await client.flush()

    with outbound_call(TELEGRAM_EDGE, "getMe", log_call=False) as call:
        call.status_code = 200

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)


async def test_outside_any_invocation_the_line_is_still_written():
    # PTB's own startup calls run with no flush window to join; losing their metrics must not cost
    # the record that says the call happened.
    with capture_logs() as logs:
        with outbound_call(TELEGRAM_EDGE, "getMe") as call:
            call.status_code = 200

    assert [entry["api_method"] for entry in logs] == ["getMe"]
