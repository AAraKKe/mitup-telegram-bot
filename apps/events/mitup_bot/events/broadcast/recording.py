"""Recording phase: resolve each claimed (IN_PROGRESS) delivery to its terminal or retry status
and release flood-halted rows. See the package docstring in `__init__.py` for the invariants."""

import datetime as dt
from collections import defaultdict

import structlog
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import BroadcastDelivery, User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

from .types import BatchResult, DeliveryOutcome, PendingDelivery

log = structlog.get_logger(__name__)

# Each recorded delivery's final status maps to the one-hot metric that reads 1 for that status;
# every other member of this map is emitted as 0 for the same delivery, so each status has a
# complete, gap-free time series to alarm and graph on regardless of which outcomes a batch saw.
DELIVERY_METRIC_BY_STATUS: dict[BroadcastDeliveryStatus, MetricKey] = {
    BroadcastDeliveryStatus.SENT: MetricKey.BROADCAST_DELIVERY_SENT,
    BroadcastDeliveryStatus.FAILED: MetricKey.BROADCAST_DELIVERY_FAILED,
    BroadcastDeliveryStatus.RETRY_PENDING: MetricKey.BROADCAST_DELIVERY_RETRY_PENDING,
    BroadcastDeliveryStatus.SKIPPED_INACTIVE: MetricKey.BROADCAST_DELIVERY_SKIPPED_INACTIVE,
}


@db.with_session
async def record_batch_outcomes(session: AsyncSession, result: BatchResult) -> int:
    """Resolve each claimed (IN_PROGRESS) delivery to its recorded outcome, and — when flood
    control halted the batch — release the untried remainder in the same transaction. Returns how
    many unreachable recipients were flipped to LEFT (0 if none).

    Terminal outcomes (SENT, FAILED, SKIPPED_INACTIVE) are written in bulk; a RETRY_PENDING
    outcome is re-parked with its own `next_attempt_time`. A skipped recipient is also flipped to
    LEFT. No metrics are emitted here: both the per-delivery telemetry (`emit_delivery_outcomes`)
    and the INACTIVE_USER_SET count must fire only after this transaction commits, so the caller
    emits them once this returns — otherwise a failed commit would leave rows IN_PROGRESS (or
    MEMBERs un-flipped) while the metrics already claimed the outcome happened.
    """
    by_status: dict[BroadcastDeliveryStatus, list[DeliveryOutcome]] = defaultdict(list)
    for outcome in result.outcomes:
        by_status[outcome.status].append(outcome)

    deactivated = 0
    if sent := by_status[BroadcastDeliveryStatus.SENT]:
        await mark_deliveries(session, sent, BroadcastDeliveryStatus.SENT, sent_time=dt.datetime.now(dt.UTC))
    if failed := by_status[BroadcastDeliveryStatus.FAILED]:
        await mark_deliveries(session, failed, BroadcastDeliveryStatus.FAILED)
    if retry_pending := by_status[BroadcastDeliveryStatus.RETRY_PENDING]:
        await schedule_retries(session, retry_pending)
    if skipped := by_status[BroadcastDeliveryStatus.SKIPPED_INACTIVE]:
        await mark_deliveries(session, skipped, BroadcastDeliveryStatus.SKIPPED_INACTIVE)
        deactivated = await deactivate_skipped_users(session, skipped)
    if result.unattempted:
        assert result.flood_backoff is not None, "Unattempted rows are only carried out under flood control"
        await release_unattempted(session, result.unattempted, result.flood_backoff)

    return deactivated


def emit_delivery_outcomes(metrics: MetricsClient, outcomes: list[DeliveryOutcome], broadcast_id: int):
    """Emit the one-hot `BroadcastDelivery*` telemetry for every recorded outcome: the matching
    status metric reads 1 and the other three read 0, so each series stays gap-free.

    Must be called post-commit (see `record_batch_outcomes`). Properties may only carry
    run-constant facets: the shared EMF logger buffers these emissions into one document per flush
    and `set_property` is last-writer-wins, so a per-delivery-varying property (e.g. attempt) would
    be clobbered and misattributed across the batch. Only `broadcast_id` (run-constant) rides here;
    per-delivery attempt investigation lives on the `broadcast_delivery` log line. Emitted as EMF
    properties, never dimensions, per the monitoring rules.
    """
    for outcome in outcomes:
        matched = DELIVERY_METRIC_BY_STATUS[outcome.status]
        properties = {"broadcast_id": broadcast_id}
        for key in DELIVERY_METRIC_BY_STATUS.values():
            metrics.emit(key, 1 if key is matched else 0, MetricUnit.COUNT, properties=properties)


async def release_unattempted(session: AsyncSession, unattempted: list[PendingDelivery], flood_backoff: dt.timedelta):
    """Return the flood-halted, still-IN_PROGRESS rows to RETRY_PENDING in one bulk UPDATE.

    The claim already bumped their `attempt_count`, but no send happened, so it is decremented
    back — their attempt budget must not be spent on a non-attempt. `next_attempt_time` is set from
    the triggering row's flood backoff. These rows are owned by this worker, so the update is
    race-free.
    """
    next_attempt_time = dt.datetime.now(dt.UTC) + flood_backoff
    delivery_ids = [pending.delivery_id for pending in unattempted]
    await session.exec(
        update(BroadcastDelivery)
        .where(col(BroadcastDelivery.id).in_(delivery_ids))
        .values(
            status=BroadcastDeliveryStatus.RETRY_PENDING,
            next_attempt_time=next_attempt_time,
            attempt_count=col(BroadcastDelivery.attempt_count) - 1,
        )
    )
    log.info(
        "Broadcast deliveries released",
        count=len(delivery_ids),
        next_attempt_time=next_attempt_time,
        attempt_refunded=True,
        reason="flood_control_halt",
    )


async def mark_deliveries(
    session: AsyncSession,
    outcomes: list[DeliveryOutcome],
    status: BroadcastDeliveryStatus,
    sent_time: dt.datetime | None = None,
):
    values: dict[str, object] = {"status": status}
    if sent_time is not None:
        values["sent_time"] = sent_time
    delivery_ids = [outcome.delivery_id for outcome in outcomes]
    await session.exec(update(BroadcastDelivery).where(col(BroadcastDelivery.id).in_(delivery_ids)).values(**values))


async def schedule_retries(session: AsyncSession, outcomes: list[DeliveryOutcome]):
    """Park each retryable delivery back on RETRY_PENDING with its own `next_attempt_time`. Rows
    carry distinct backoffs, so each is written on its own — retries are the exceptional path."""
    for outcome in outcomes:
        await session.exec(
            update(BroadcastDelivery)
            .where(col(BroadcastDelivery.id) == outcome.delivery_id)
            .values(status=BroadcastDeliveryStatus.RETRY_PENDING, next_attempt_time=outcome.next_attempt_time)
        )


async def deactivate_skipped_users(session: AsyncSession, skipped: list[DeliveryOutcome]) -> int:
    """Flip unreachable MEMBERs to LEFT via `User.mark_inactive`; return how many transitioned.

    The INACTIVE_USER_SET metric is emitted by the caller post-commit (see `record_batch_outcomes`),
    not here — a rolled-back flip must not report users as deactivated.
    """
    user_ids = [outcome.user_id for outcome in skipped]
    users = (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()
    left = sum(user.mark_inactive() for user in users)
    if left:
        log.info("Broadcast marked unreachable recipients inactive", count=left)
    return left
