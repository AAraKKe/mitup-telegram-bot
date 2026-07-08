import datetime as dt
from unittest import mock

import pytest

from mitup_bot.cli.broadcast import runner
from mitup_bot.cli.broadcast.types import (
    MAX_BROADCAST_ATTEMPTS,
    BatchResult,
    ClaimedBroadcast,
    DeliveryOutcome,
    PendingDelivery,
)
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.helpers import MockApi, create_member
from tests.helpers.monitoring import MetricAssertions

SENT = BroadcastDeliveryStatus.SENT
RETRY_PENDING = BroadcastDeliveryStatus.RETRY_PENDING


async def test_run_returns_early_when_nothing_claimed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "claim_next_broadcast", mock.AsyncMock(return_value=None))
    process = mock.AsyncMock()
    monkeypatch.setattr(runner, "process_claimed_broadcast", process)

    await runner.run(api, metrics_client, [1])

    process.assert_not_awaited()


async def test_run_delegates_to_processing_when_claimed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)
    monkeypatch.setattr(runner, "claim_next_broadcast", mock.AsyncMock(return_value=claimed))
    process = mock.AsyncMock()
    monkeypatch.setattr(runner, "process_claimed_broadcast", process)

    await runner.run(api, metrics_client, [1])

    process.assert_awaited_once_with(api, metrics_client, [1], claimed)


async def test_process_claimed_broadcast_terminal_failure_finalizes_failed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    finalize = mock.AsyncMock()
    monkeypatch.setattr(runner, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=MAX_BROADCAST_ATTEMPTS + 1, terminal_failure=True)

    await runner.process_claimed_broadcast(api, metrics_client, [1], claimed)

    finalize.assert_awaited_once_with(api, metrics_client, [1], 5, BroadcastStatus.FAILED)


@pytest.mark.parametrize("freshly_materialized", [True, False], ids=["fresh", "resumed"])
async def test_process_claimed_broadcast_normal_path(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
    freshly_materialized: bool,
):
    monkeypatch.setattr(runner, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(runner, "materialize_audience", mock.AsyncMock(return_value=(4, freshly_materialized)))
    send_all = mock.AsyncMock()
    monkeypatch.setattr(runner, "send_all_pending", send_all)
    monkeypatch.setattr(runner, "defer_for_pending_retries", mock.AsyncMock(return_value=False))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(runner, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)

    await runner.process_claimed_broadcast(api, metrics_client, [1], claimed)
    await metrics_client.flush()

    send_all.assert_awaited_once_with(api, metrics_client, 5, {"en": "hi"})
    finalize.assert_awaited_once_with(api, metrics_client, [1], 5, BroadcastStatus.DONE)
    if freshly_materialized:
        metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_TO_SEND, value=4)
    else:
        metrics.assert_not_emitted(name=MetricKey.BROADCAST_MESSAGES_TO_SEND)


async def test_process_claimed_broadcast_defers_finalization_when_retries_pending(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(runner, "materialize_audience", mock.AsyncMock(return_value=(4, False)))
    monkeypatch.setattr(runner, "send_all_pending", mock.AsyncMock())
    monkeypatch.setattr(runner, "defer_for_pending_retries", mock.AsyncMock(return_value=True))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(runner, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)

    await runner.process_claimed_broadcast(api, metrics_client, [1], claimed)

    # Deliveries still await retry, so the broadcast is left SENDING for the next tick — no finalize.
    finalize.assert_not_awaited()


@pytest.mark.parametrize("unfinished, expected", [(0, False), (3, True)], ids=["all_done", "retries_pending"])
async def test_defer_for_pending_retries_resets_attempts_only_when_unfinished(
    monkeypatch: pytest.MonkeyPatch, unfinished: int, expected: bool
):
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=unfinished))
    reset = mock.AsyncMock()
    monkeypatch.setattr(runner, "reset_broadcast_attempts", reset)

    deferred = await runner.defer_for_pending_retries(5)

    assert deferred is expected
    if expected:
        reset.assert_awaited_once_with(5)
    else:
        reset.assert_not_awaited()


async def test_send_all_pending_drains_until_no_batch(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    claim = mock.AsyncMock(side_effect=[batch, []])
    monkeypatch.setattr(runner, "claim_pending_batch", claim)
    deliver = mock.AsyncMock(return_value=BatchResult(outcomes=[DeliveryOutcome(1, 1, SENT)], flood_control=False))
    monkeypatch.setattr(runner, "deliver_batch", deliver)
    record = mock.AsyncMock()
    monkeypatch.setattr(runner, "record_batch_outcomes", record)

    await runner.send_all_pending(api, metrics_client, 5, {"en": "hi"})
    await metrics_client.flush()

    assert claim.await_count == 2
    deliver.assert_awaited_once()
    record.assert_awaited_once()
    # Nothing was a re-claimed retry (attempt_count 1), so the retried metric is not emitted.
    metrics.assert_not_emitted(name=MetricKey.BROADCAST_MESSAGES_RETRIED)


async def test_send_all_pending_stops_claiming_after_flood_control(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    claim = mock.AsyncMock(side_effect=[batch, batch, []])
    monkeypatch.setattr(runner, "claim_pending_batch", claim)
    deliver = mock.AsyncMock(
        return_value=BatchResult(
            outcomes=[DeliveryOutcome(1, 1, RETRY_PENDING, dt.datetime.now(dt.UTC))], flood_control=True
        )
    )
    monkeypatch.setattr(runner, "deliver_batch", deliver)
    monkeypatch.setattr(runner, "record_batch_outcomes", mock.AsyncMock())

    await runner.send_all_pending(api, metrics_client, 5, {"en": "hi"})

    # The first batch tripped flood control, so the drain loop breaks — no second claim.
    assert claim.await_count == 1
    deliver.assert_awaited_once()


async def test_send_all_pending_emits_retried_metric_for_reclaimed_deliveries(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    reclaimed = [
        PendingDelivery(1, create_member(1, 11, "en"), "en", 2),
        PendingDelivery(2, create_member(2, 12, "en"), "en", 3),
    ]
    monkeypatch.setattr(runner, "claim_pending_batch", mock.AsyncMock(side_effect=[reclaimed, []]))
    monkeypatch.setattr(
        runner,
        "deliver_batch",
        mock.AsyncMock(return_value=BatchResult(outcomes=[DeliveryOutcome(1, 1, SENT)], flood_control=False)),
    )
    monkeypatch.setattr(runner, "record_batch_outcomes", mock.AsyncMock())

    await runner.send_all_pending(api, metrics_client, 5, {"en": "hi"})
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_RETRIED, value=2)
