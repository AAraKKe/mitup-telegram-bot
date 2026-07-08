"""Run phase: the recurrent-events entry point that drains one broadcast per tick and drives it
through claim, deliver, record, and finalize. See the package docstring in `__init__.py`."""

import structlog

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

from .claiming import (
    claim_next_broadcast,
    claim_pending_batch,
    count_unfinished_deliveries,
    load_broadcast_bodies,
    materialize_audience,
    reset_broadcast_attempts,
)
from .delivery import deliver_batch
from .finalize import finalize_and_report
from .recording import record_batch_outcomes
from .types import ClaimedBroadcast

log = structlog.get_logger(__name__)


async def run(api: TelegramApiWrapper, metrics: MetricsClient, admin_tg_ids: list[int]):
    """Send at most one queued (or resume one in-flight) broadcast; the next tick handles the next.

    A systemic failure (e.g. a `NetworkError`) propagates so the run is marked faulty and the
    broadcast is left `SENDING` for the next tick to resume — per-recipient failures never
    propagate, they are counted and rolled up at finalization.
    """
    claimed = await claim_next_broadcast()
    if claimed is None:
        return

    with structlog.contextvars.bound_contextvars(broadcast_id=claimed.broadcast_id):
        await process_claimed_broadcast(api, metrics, admin_tg_ids, claimed)


async def process_claimed_broadcast(
    api: TelegramApiWrapper, metrics: MetricsClient, admin_tg_ids: list[int], claimed: ClaimedBroadcast
):
    if claimed.terminal_failure:
        log.warning("Broadcast exceeded the attempt threshold, failing it", attempts=claimed.attempts)
        await finalize_and_report(api, metrics, admin_tg_ids, claimed.broadcast_id, BroadcastStatus.FAILED)
        return

    bodies = await load_broadcast_bodies(claimed.broadcast_id)
    total, freshly_materialized = await materialize_audience(claimed.broadcast_id, list(bodies))
    if freshly_materialized:
        metrics.emit(MetricKey.BROADCAST_MESSAGES_TO_SEND, total, MetricUnit.COUNT)

    await send_all_pending(api, metrics, claimed.broadcast_id, bodies)
    if await defer_for_pending_retries(claimed.broadcast_id):
        return
    await finalize_and_report(api, metrics, admin_tg_ids, claimed.broadcast_id, BroadcastStatus.DONE)


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


async def send_all_pending(api: TelegramApiWrapper, metrics: MetricsClient, broadcast_id: int, bodies: dict[str, str]):
    """Drain every due delivery, one atomically-claimed `BROADCAST_BATCH_SIZE` page at a time,
    until no claim returns any rows — or until Telegram flood control fires, which stops the run
    so the next tick resumes after the backoff. Re-claimed retry deliveries (attempt > 1) are
    counted and emitted once per run."""
    retried = 0
    while batch := await claim_pending_batch(broadcast_id):
        result = await deliver_batch(api, broadcast_id, batch, bodies)
        await record_batch_outcomes(result, metrics)
        # Only rows we actually attempted count as retries; the released remainder gets its claim
        # increment undone and is counted if and when it is genuinely re-claimed on a later run.
        unattempted_ids = {pending.delivery_id for pending in result.unattempted}
        retried += sum(pending.attempt_count > 1 for pending in batch if pending.delivery_id not in unattempted_ids)
        if result.flood_control:
            log.warning("Broadcast paused batch draining after flood control", broadcast_id=broadcast_id)
            break
    if retried:
        metrics.emit(MetricKey.BROADCAST_MESSAGES_RETRIED, retried, MetricUnit.COUNT)
