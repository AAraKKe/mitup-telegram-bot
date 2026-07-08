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


@db.with_session
async def record_batch_outcomes(session: AsyncSession, result: BatchResult, metrics: MetricsClient):
    """Resolve each claimed (IN_PROGRESS) delivery to its recorded outcome, and — when flood
    control halted the batch — release the untried remainder in the same transaction.

    Terminal outcomes (SENT, FAILED, SKIPPED_INACTIVE) are written in bulk; a RETRY_PENDING
    outcome is re-parked with its own `next_attempt_time`. A skipped recipient is also flipped to
    LEFT.
    """
    by_status: dict[BroadcastDeliveryStatus, list[DeliveryOutcome]] = defaultdict(list)
    for outcome in result.outcomes:
        by_status[outcome.status].append(outcome)

    if sent := by_status[BroadcastDeliveryStatus.SENT]:
        await mark_deliveries(session, sent, BroadcastDeliveryStatus.SENT, sent_time=dt.datetime.now(dt.UTC))
    if failed := by_status[BroadcastDeliveryStatus.FAILED]:
        await mark_deliveries(session, failed, BroadcastDeliveryStatus.FAILED)
    if retry_pending := by_status[BroadcastDeliveryStatus.RETRY_PENDING]:
        await schedule_retries(session, retry_pending)
    if skipped := by_status[BroadcastDeliveryStatus.SKIPPED_INACTIVE]:
        await mark_deliveries(session, skipped, BroadcastDeliveryStatus.SKIPPED_INACTIVE)
        await deactivate_skipped_users(session, skipped, metrics)
    if result.unattempted:
        assert result.flood_backoff is not None, "Unattempted rows are only carried out under flood control"
        await release_unattempted(session, result.unattempted, result.flood_backoff)


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


async def deactivate_skipped_users(session: AsyncSession, skipped: list[DeliveryOutcome], metrics: MetricsClient):
    """Flip unreachable MEMBERs to LEFT via `User.mark_inactive`."""
    user_ids = [outcome.user_id for outcome in skipped]
    users = (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()
    left = sum(user.mark_inactive() for user in users)
    if left:
        log.info("Broadcast marked unreachable recipients inactive", count=left)
        metrics.emit(MetricKey.INACTIVE_USER_SET, left, MetricUnit.COUNT)
