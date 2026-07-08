"""Finalize phase: roll the delivery table into per-language counts, transition the broadcast to
its terminal status, purge, and notify. See the package docstring in `__init__.py` for the
concurrent-finalization and orphan invariants."""

import datetime as dt

import structlog
from sqlmodel import and_, col, delete, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import Broadcast, BroadcastDelivery
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

from .reporting import notify_operators
from .types import BroadcastSummary, LanguageBreakdown

log = structlog.get_logger(__name__)


async def finalize_and_report(
    api: TelegramApiWrapper,
    metrics: MetricsClient,
    admin_tg_ids: list[int],
    broadcast_id: int,
    final_status: BroadcastStatus,
):
    """Roll up counts, purge, and — exactly once — notify the operators.

    Two live workers can both fully drain the same broadcast's delivery queue (see the module
    docstring), so both reach finalization for the same `broadcast_id`. The count rollups and
    purge are idempotent (recomputed/deleted from the same table state either way), but the
    operator DM must fire once: `finalize_broadcast`'s compare-and-swap on `status still SENDING`
    reports which call actually performed the terminal transition, and only that one notifies.
    """
    summary, won_transition = await finalize_broadcast(broadcast_id, final_status)
    metrics.emit(MetricKey.BROADCAST_MESSAGES_SENT, summary.sent, MetricUnit.COUNT)
    metrics.emit(MetricKey.BROADCAST_MESSAGES_FAILED, summary.failed, MetricUnit.COUNT)
    metrics.emit(MetricKey.BROADCAST_MESSAGES_SKIPPED, summary.skipped, MetricUnit.COUNT)
    metrics.emit(MetricKey.BROADCAST_MESSAGES_ORPHANED, summary.orphaned, MetricUnit.COUNT)
    if summary.orphaned:
        log.warning(
            "Broadcast finalized with orphaned deliveries", broadcast_id=broadcast_id, orphaned=summary.orphaned
        )

    # Purge before notifying: the finalized broadcast's per-language counts are already committed
    # onto Broadcast/BroadcastMessage, so cleanup must not depend on the operator DM succeeding —
    # a failed summary send must never leak the delivery rows.
    await purge_deliveries(broadcast_id)
    if won_transition:
        await notify_operators(api, admin_tg_ids, summary)


@db.with_session
async def finalize_broadcast(
    session: AsyncSession, broadcast_id: int, final_status: BroadcastStatus
) -> tuple[BroadcastSummary, bool]:
    """Roll the delivery table up into per-language and total counts, then close the broadcast.

    Any delivery still PENDING or RETRY_PENDING at this point is marked FAILED before aggregation
    (see `fail_unattempted_deliveries`) — a no-op on the normal drained path, and on the
    terminal-failure path it records never-completed recipients as genuine non-deliveries. Rows
    left IN_PROGRESS are a different case entirely — claimed by a worker that crashed before
    recording an outcome — and are counted separately as orphans, never as FAILED.

    Counts come exclusively from aggregating `broadcast_deliveries` (grouped by language and
    status), never from in-memory accumulators, so a broadcast resumed across several runs still
    finalizes with the true totals. Returns whether this call performed the SENDING -> terminal
    transition (see `transition_to_terminal`).
    """
    await fail_unattempted_deliveries(session, broadcast_id)
    counts = await aggregate_delivery_counts(session, broadcast_id)
    broadcast = await session.get(Broadcast, broadcast_id)
    assert broadcast is not None, "The claimed broadcast row must still exist"

    breakdown = build_language_breakdown(broadcast, counts)
    broadcast.sent_count = sum(line.sent for line in breakdown)
    broadcast.failed_count = sum(line.failed for line in breakdown)
    broadcast.skipped_count = sum(line.skipped for line in breakdown)
    broadcast.orphan_count = sum(line.orphaned for line in breakdown)

    won_transition = await transition_to_terminal(session, broadcast_id, final_status)

    summary = BroadcastSummary(
        name=broadcast.name,
        status=final_status,
        attempts=broadcast.attempts,
        total=broadcast.total_recipients or 0,
        sent=broadcast.sent_count,
        failed=broadcast.failed_count,
        skipped=broadcast.skipped_count,
        orphaned=broadcast.orphan_count,
        breakdown=breakdown,
    )
    return summary, won_transition


async def fail_unattempted_deliveries(session: AsyncSession, broadcast_id: int):
    """Flip every still-PENDING or awaiting-RETRY_PENDING delivery to FAILED before aggregation.

    A no-op on the normal drained path (the finalize gate only lets finalization run once none
    remain); on the terminal-failure path it records these never-completed recipients as genuine
    non-deliveries. IN_PROGRESS orphans are deliberately left untouched — their outcome is unknown.
    """
    await session.exec(
        update(BroadcastDelivery)
        .where(
            and_(
                col(BroadcastDelivery.broadcast_id) == broadcast_id,
                col(BroadcastDelivery.status).in_(
                    [BroadcastDeliveryStatus.PENDING, BroadcastDeliveryStatus.RETRY_PENDING]
                ),
            )
        )
        .values(status=BroadcastDeliveryStatus.FAILED)
    )


def build_language_breakdown(
    broadcast: Broadcast, counts: dict[tuple[str, BroadcastDeliveryStatus], int]
) -> list[LanguageBreakdown]:
    breakdown: list[LanguageBreakdown] = []
    for message in broadcast.messages:
        message.sent_count = counts.get((message.language, BroadcastDeliveryStatus.SENT), 0)
        message.failed_count = counts.get((message.language, BroadcastDeliveryStatus.FAILED), 0)
        message.skipped_count = counts.get((message.language, BroadcastDeliveryStatus.SKIPPED_INACTIVE), 0)
        message.orphan_count = counts.get((message.language, BroadcastDeliveryStatus.IN_PROGRESS), 0)
        breakdown.append(
            LanguageBreakdown(
                language=message.language,
                sent=message.sent_count,
                failed=message.failed_count,
                skipped=message.skipped_count,
                orphaned=message.orphan_count,
            )
        )
    return breakdown


async def transition_to_terminal(session: AsyncSession, broadcast_id: int, final_status: BroadcastStatus) -> bool:
    """Atomically flip SENDING -> a terminal status; returns whether this call performed it.

    A plain ORM attribute assignment (`broadcast.status = final_status`) would let two concurrent
    finalize calls both "win" — each reads SENDING in its own transaction with no lock, then both
    write. This compare-and-swap UPDATE carries its own WHERE guard, so Postgres serializes the
    two statements: whichever commits first flips the row away from SENDING, and the other's
    UPDATE then matches zero rows.
    """
    result = await session.exec(
        update(Broadcast)
        .where(and_(col(Broadcast.id) == broadcast_id, col(Broadcast.status) == BroadcastStatus.SENDING))
        .values(status=final_status, completed_time=dt.datetime.now(dt.UTC))
    )
    return result.rowcount == 1


async def aggregate_delivery_counts(
    session: AsyncSession, broadcast_id: int
) -> dict[tuple[str, BroadcastDeliveryStatus], int]:
    statement = (
        select(col(BroadcastDelivery.language_sent), col(BroadcastDelivery.status), func.count())
        .where(col(BroadcastDelivery.broadcast_id) == broadcast_id)
        .group_by(col(BroadcastDelivery.language_sent), col(BroadcastDelivery.status))
    )
    rows = (await session.exec(statement)).all()
    return {(language, status): count for language, status, count in rows}


@db.with_session
async def purge_deliveries(session: AsyncSession, broadcast_id: int):
    """Drop every delivery row once the broadcast is terminal — the table has no retention."""
    await session.exec(delete(BroadcastDelivery).where(col(BroadcastDelivery.broadcast_id) == broadcast_id))
