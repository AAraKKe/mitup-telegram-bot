"""Run phase: the recurrent-events entry point that drains one broadcast per tick and drives it
through claim, deliver, record, and finalize. See the package docstring in `__init__.py`."""

import structlog

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

from .claiming import (
    claim_next_broadcast,
    claim_pending_batch,
    count_broadcast_backlog,
    count_unfinished_deliveries,
    load_broadcast_bodies,
    materialize_audience,
    reset_broadcast_attempts,
)
from .delivery import build_recipient_views, deliver_batch
from .finalize import finalize_and_report
from .recording import emit_delivery_outcomes, record_batch_outcomes
from .types import BatchResult, ClaimedBroadcast

log = structlog.get_logger(__name__)


async def run(api: TelegramApiWrapper, metrics: MetricsClient, admin_tg_ids: list[int]):
    """Send at most one queued (or resume one in-flight) broadcast; the next tick handles the next.

    A systemic failure (e.g. a `NetworkError`) propagates so the run is marked faulty and the
    broadcast is left `SENDING` for the next tick to resume — per-recipient failures never
    propagate, they are counted and rolled up at finalization.
    """
    await emit_backlog_gauges(metrics)
    claimed = await claim_next_broadcast()
    if claimed is None:
        return

    with structlog.contextvars.bound_contextvars(broadcast_id=claimed.broadcast_id):
        await process_claimed_broadcast(api, metrics, admin_tg_ids, claimed)


async def emit_backlog_gauges(metrics: MetricsClient):
    """Emit the queue-depth gauges, but only on ticks that have backlog to report.

    Broadcasts are rare operator-triggered events, so an always-on zero baseline would be permanent
    noise; a quiet system emits nothing. While anything is QUEUED, SENDING or parked RETRY_PENDING,
    all three gauges emit every tick (zeros included for the quiet ones) so an active run — or a
    stuck queue — is a live, complete reading.
    """
    queued, sending, retry_pending = await count_broadcast_backlog()
    if not (queued or sending or retry_pending):
        return
    metrics.emit(MetricKey.BROADCASTS_QUEUED, queued, MetricUnit.COUNT)
    metrics.emit(MetricKey.BROADCASTS_SENDING, sending, MetricUnit.COUNT)
    metrics.emit(MetricKey.BROADCAST_DELIVERIES_RETRY_PENDING, retry_pending, MetricUnit.COUNT)


async def process_claimed_broadcast(
    api: TelegramApiWrapper, metrics: MetricsClient, admin_tg_ids: list[int], claimed: ClaimedBroadcast
):
    if claimed.terminal_failure:
        log.warning("Broadcast exceeded the attempt threshold, failing it", attempts=claimed.attempts)
        await finalize_and_report(
            api, metrics, admin_tg_ids, claimed.author_tg_id, claimed.broadcast_id, BroadcastStatus.FAILED
        )
        return

    bodies = await load_broadcast_bodies(claimed.broadcast_id)
    total = await materialize_audience(claimed.broadcast_id, list(bodies))
    # An initial progress datapoint doubles as the "broadcast started/resumed" marker that dropping
    # BROADCAST_MESSAGES_TO_SEND removed: on a resumed broadcast it reads the true current percent,
    # and it is the only progress signal on a tick where no delivery is due yet. Flushed right away
    # so it owns its flush window — the shared EMF logger is last-writer-wins on properties, and
    # sharing a window with the first batch would clobber this datapoint's total/remaining.
    if total:
        await emit_progress(metrics, claimed.broadcast_id, total)
        await metrics.flush()

    await send_all_pending(api, metrics, claimed.broadcast_id, total, bodies)
    if await defer_for_pending_retries(claimed.broadcast_id):
        return
    await finalize_and_report(
        api, metrics, admin_tg_ids, claimed.author_tg_id, claimed.broadcast_id, BroadcastStatus.DONE
    )


async def defer_for_pending_retries(broadcast_id: int) -> bool:
    """Hold finalization while any delivery is still PENDING or RETRY_PENDING (due or not).

    Resets `Broadcast.attempts` to 0 so a broadcast waiting out retry backoff across ticks never
    creeps toward `MAX_BROADCAST_ATTEMPTS` — that counter guards against a worker crash-loop, not
    against legitimate backoff. The broadcast stays SENDING and the next tick re-claims it.
    """
    unfinished = await count_unfinished_deliveries(broadcast_id)
    if not unfinished:
        return False
    log.info("Broadcast deferred for pending retries", broadcast_id=broadcast_id, pending=unfinished)
    await reset_broadcast_attempts(broadcast_id)
    return True


async def send_all_pending(
    api: TelegramApiWrapper, metrics: MetricsClient, broadcast_id: int, total: int, bodies: dict[str, str]
):
    """Drain every due delivery, one atomically-claimed `BROADCAST_BATCH_SIZE` page at a time,
    until no claim returns any rows — or until Telegram flood control fires, which stops the run
    so the next tick resumes after the backoff.

    The recipient view is precomputed once per language (see `build_recipient_views`) rather than
    rebuilt per recipient. Per batch: outcomes are recorded and committed first, THEN the
    per-delivery telemetry is emitted (post-commit — a rolled-back batch must not claim outcomes),
    then live progress, then a flush. Flushing per batch is what makes the telemetry live: one EMF
    document per batch gives each key its own timestamp so the dashboard shows a real progression
    (nothing reaches CloudWatch until a flush, and the only other flush is once at run end). It also
    bounds each one-hot delivery key to at most `BROADCAST_BATCH_SIZE` (50) array values per EMF
    document, comfortably under EMF's 100-value-per-metric limit.
    """
    views = build_recipient_views(bodies)
    while batch := await claim_pending_batch(broadcast_id):
        result = await deliver_batch(api, broadcast_id, batch, views)
        deactivated = await record_batch_outcomes(result)
        # Both post-commit (see record_batch_outcomes): the per-delivery one-hot telemetry, and the
        # count of MEMBERs this batch flipped to LEFT (only when > 0, matching the historic emit).
        emit_delivery_outcomes(metrics, result.outcomes, broadcast_id)
        if deactivated:
            metrics.emit(MetricKey.INACTIVE_USER_SET, deactivated, MetricUnit.COUNT)
        await emit_batch_progress(metrics, broadcast_id, total, result)
        await metrics.flush()
        if result.flood_control:
            log.warning("Broadcast paused batch draining after flood control", broadcast_id=broadcast_id)
            break


async def emit_progress(metrics: MetricsClient, broadcast_id: int, total: int) -> tuple[float | None, int]:
    """Emit the live 0-100 `BroadcastProgressPercent` reading, computed straight from the delivery
    table — never an in-memory accumulator — so it stays correct across the runs a long broadcast
    resumes over, and monotonic-ish because a retried row stays in the unfinished count until it
    truly lands. Returns `(percent, remaining)`.

    `total`/`remaining` ride as properties, which is safe only because this metric is emitted once
    per flush window (each batch flushes immediately); the shared EMF logger is last-writer-wins on
    properties, so a metric emitted multiple times per window could not carry per-emission context.
    The percent series is skipped when `total` is 0 (unknown recipient count). A row still
    IN_PROGRESS reads as "done" in this math (it is not in `remaining`) — its outcome is unknown and
    never retried, so it is deliberately not held against completion; the finalize orphan warning
    and the summary DM carry those.
    """
    remaining = await count_unfinished_deliveries(broadcast_id)
    percent = round((total - remaining) / total * 100, 1) if total else None
    if percent is not None:
        metrics.emit(
            MetricKey.BROADCAST_PROGRESS_PERCENT,
            percent,
            MetricUnit.PERCENT,
            properties={"broadcast_id": broadcast_id, "total": total, "remaining": remaining},
        )
    return percent, remaining


async def emit_batch_progress(metrics: MetricsClient, broadcast_id: int, total: int, result: BatchResult):
    """Emit per-batch throughput (`BroadcastBatchMessagesSent`) plus the live progress reading, and
    log the per-batch line the infra queries key on."""
    sent = sum(outcome.status is BroadcastDeliveryStatus.SENT for outcome in result.outcomes)
    failed = sum(outcome.status is BroadcastDeliveryStatus.FAILED for outcome in result.outcomes)
    retry = sum(outcome.status is BroadcastDeliveryStatus.RETRY_PENDING for outcome in result.outcomes)
    skipped = sum(outcome.status is BroadcastDeliveryStatus.SKIPPED_INACTIVE for outcome in result.outcomes)

    metrics.emit(
        MetricKey.BROADCAST_BATCH_MESSAGES_SENT, sent, MetricUnit.COUNT, properties={"broadcast_id": broadcast_id}
    )
    percent, remaining = await emit_progress(metrics, broadcast_id, total)
    log.info(
        "Broadcast batch recorded",
        sent=sent,
        failed=failed,
        retry=retry,
        skipped=skipped,
        percent=percent,
        remaining=remaining,
    )
