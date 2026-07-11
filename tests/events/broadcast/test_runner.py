import datetime as dt
from unittest import mock

import pytest
from structlog.testing import capture_logs

from mitup_bot.events.broadcast import runner
from mitup_bot.events.broadcast.types import (
    MAX_BROADCAST_ATTEMPTS,
    BatchResult,
    ClaimedBroadcast,
    DeliveryOutcome,
    PendingDelivery,
)
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from tests.helpers import MockApi, create_member
from tests.helpers.monitoring import MetricAssertions

SENT = BroadcastDeliveryStatus.SENT
FAILED = BroadcastDeliveryStatus.FAILED
SKIPPED = BroadcastDeliveryStatus.SKIPPED_INACTIVE
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
    claimed = ClaimedBroadcast(broadcast_id=5, author_tg_id=99, attempts=1, terminal_failure=False)
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
    claimed = ClaimedBroadcast(
        broadcast_id=5, author_tg_id=42, attempts=MAX_BROADCAST_ATTEMPTS + 1, terminal_failure=True
    )

    await runner.process_claimed_broadcast(api, metrics_client, [1], claimed)

    # The metrics client and author id are threaded through: metrics so finalize can emit the
    # bulk-failed delivery count, the author id so the summary can prefer the author over the admins.
    finalize.assert_awaited_once_with(api, metrics_client, [1], 42, 5, BroadcastStatus.FAILED)


async def test_process_claimed_broadcast_normal_path_emits_initial_progress(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(runner, "materialize_audience", mock.AsyncMock(return_value=4))
    # A fresh audience: every one of the 4 recipients is still unfinished, so the initial marker
    # reads 0%.
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=4))
    send_all = mock.AsyncMock()
    monkeypatch.setattr(runner, "send_all_pending", send_all)
    monkeypatch.setattr(runner, "defer_for_pending_retries", mock.AsyncMock(return_value=False))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(runner, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, author_tg_id=42, attempts=1, terminal_failure=False)

    await runner.process_claimed_broadcast(api, metrics_client, [1], claimed)
    await metrics_client.flush()

    # An initial progress datapoint is emitted right after materialization (the started/resumed
    # marker), then the total is threaded into the drain, and finalize gets the metrics client.
    metrics.assert_emitted(
        name=MetricKey.BROADCAST_PROGRESS_PERCENT,
        value=0.0,
        unit=MetricUnit.PERCENT,
        properties={"broadcast_id": 5, "total": 4, "remaining": 4},
    )
    send_all.assert_awaited_once_with(api, metrics_client, 5, 4, {"en": "hi"})
    finalize.assert_awaited_once_with(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)


async def test_process_claimed_broadcast_defers_finalization_when_retries_pending(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(runner, "materialize_audience", mock.AsyncMock(return_value=4))
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=4))
    monkeypatch.setattr(runner, "send_all_pending", mock.AsyncMock())
    monkeypatch.setattr(runner, "defer_for_pending_retries", mock.AsyncMock(return_value=True))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(runner, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, author_tg_id=42, attempts=1, terminal_failure=False)

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
    record = mock.AsyncMock(return_value=0)
    monkeypatch.setattr(runner, "record_batch_outcomes", record)
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=0))

    await runner.send_all_pending(api, metrics_client, 5, 1, {"en": "hi"})
    await metrics_client.flush()

    assert claim.await_count == 2
    deliver.assert_awaited_once()
    # Outcomes are recorded (committed) with no metrics — the per-delivery telemetry fires
    # separately, post-commit.
    record.assert_awaited_once_with(deliver.return_value)
    # One batch drained the single recipient: one sent, and the broadcast reads 100% complete.
    metrics.assert_emitted(name=MetricKey.BROADCAST_BATCH_MESSAGES_SENT, value=1, properties={"broadcast_id": 5})
    metrics.assert_emitted(name=MetricKey.BROADCAST_PROGRESS_PERCENT, value=100.0, unit=MetricUnit.PERCENT)
    # The per-delivery one-hot telemetry fired post-commit for the single SENT outcome.
    metrics.assert_emitted(name=MetricKey.BROADCAST_DELIVERY_SENT, value=1, properties={"broadcast_id": 5})
    # No skipped recipients this batch, so no INACTIVE_USER_SET.
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_send_all_pending_stops_claiming_after_flood_control(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    claim = mock.AsyncMock(side_effect=[batch, batch, []])
    monkeypatch.setattr(runner, "claim_pending_batch", claim)
    deliver = mock.AsyncMock(
        return_value=BatchResult(
            outcomes=[DeliveryOutcome(1, 1, RETRY_PENDING, dt.datetime.now(dt.UTC))],
            flood_control=True,
        )
    )
    monkeypatch.setattr(runner, "deliver_batch", deliver)
    monkeypatch.setattr(runner, "record_batch_outcomes", mock.AsyncMock(return_value=0))
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=2))

    await runner.send_all_pending(api, metrics_client, 5, 2, {"en": "hi"})
    await metrics_client.flush()

    # The first batch tripped flood control, so the drain loop breaks — no second claim.
    assert claim.await_count == 1
    deliver.assert_awaited_once()
    # Progress is still emitted for the flood-halted batch: nothing sent, half still remaining.
    metrics.assert_emitted(name=MetricKey.BROADCAST_BATCH_MESSAGES_SENT, value=0, properties={"broadcast_id": 5})
    metrics.assert_emitted(name=MetricKey.BROADCAST_PROGRESS_PERCENT, value=0.0, unit=MetricUnit.PERCENT)


async def test_send_all_pending_emits_inactive_user_set_post_commit(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    monkeypatch.setattr(runner, "claim_pending_batch", mock.AsyncMock(side_effect=[batch, []]))
    monkeypatch.setattr(
        runner,
        "deliver_batch",
        mock.AsyncMock(return_value=BatchResult(outcomes=[DeliveryOutcome(1, 1, SKIPPED)], flood_control=False)),
    )
    # record_batch_outcomes reports two recipients flipped to LEFT this batch (post-commit).
    monkeypatch.setattr(runner, "record_batch_outcomes", mock.AsyncMock(return_value=2))
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=0))

    await runner.send_all_pending(api, metrics_client, 5, 1, {"en": "hi"})
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=2, unit=MetricUnit.COUNT)


async def test_emit_batch_progress_emits_throughput_and_percent_from_the_delivery_table(
    metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    result = BatchResult(
        outcomes=[
            DeliveryOutcome(1, 1, SENT),
            DeliveryOutcome(2, 2, SENT),
            DeliveryOutcome(3, 3, FAILED),
            DeliveryOutcome(4, 4, SKIPPED),
            DeliveryOutcome(5, 5, RETRY_PENDING, dt.datetime.now(dt.UTC)),
        ],
        flood_control=False,
    )
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=4))

    with capture_logs() as logs:
        await runner.emit_batch_progress(metrics_client, 5, 10, result)
        await metrics_client.flush()

    # Two of the five outcomes were SENT.
    metrics.assert_emitted(
        name=MetricKey.BROADCAST_BATCH_MESSAGES_SENT, value=2, unit=MetricUnit.COUNT, properties={"broadcast_id": 5}
    )
    # 4 of 10 recipients still unfinished -> 60% complete.
    metrics.assert_emitted(
        name=MetricKey.BROADCAST_PROGRESS_PERCENT,
        value=60.0,
        unit=MetricUnit.PERCENT,
        properties={"broadcast_id": 5, "total": 10, "remaining": 4},
    )
    entry = next(log for log in logs if log["event"] == "Broadcast batch recorded")
    assert (entry["sent"], entry["failed"], entry["retry"], entry["skipped"]) == (2, 1, 1, 1)
    assert (entry["percent"], entry["remaining"]) == (60.0, 4)


async def test_emit_batch_progress_skips_percent_when_total_is_zero(
    metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    result = BatchResult(outcomes=[DeliveryOutcome(1, 1, SENT)], flood_control=False)
    monkeypatch.setattr(runner, "count_unfinished_deliveries", mock.AsyncMock(return_value=0))

    await runner.emit_batch_progress(metrics_client, 5, 0, result)
    await metrics_client.flush()

    # Throughput is still reported, but a 0 total cannot yield a percentage — that series is skipped.
    metrics.assert_emitted(name=MetricKey.BROADCAST_BATCH_MESSAGES_SENT, value=1, properties={"broadcast_id": 5})
    metrics.assert_not_emitted(name=MetricKey.BROADCAST_PROGRESS_PERCENT)
