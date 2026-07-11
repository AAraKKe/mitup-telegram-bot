from unittest import mock

from mitup_bot.events.broadcast.types import BatchResult, BroadcastSummary, DeliveryOutcome, LanguageBreakdown
from mitup_bot.models.broadcasts import BroadcastStatus
from tests.helpers import MockDbSession, Result


def make_summary(
    *,
    status: BroadcastStatus,
    broadcast_id: int = 5,
    name: str = "Campaign",
    attempts: int = 1,
    total: int = 4,
    sent: int = 3,
    failed: int = 1,
    skipped: int = 0,
    orphaned: int = 0,
    breakdown: list[LanguageBreakdown] | None = None,
) -> BroadcastSummary:
    return BroadcastSummary(
        broadcast_id=broadcast_id,
        name=name,
        status=status,
        attempts=attempts,
        total=total,
        sent=sent,
        failed=failed,
        skipped=skipped,
        orphaned=orphaned,
        breakdown=breakdown if breakdown is not None else [],
    )


def script_exec(mock_session: MockDbSession, *results: Result):
    """Replace the mock session's `exec` with a scripted queue of results consumed in call order.

    The sender's inserts, UPDATE...RETURNING claims, aggregates and a repeated `count(*)` (once
    before and once after materialization) can't all be keyed by SQL string — the two counts share
    a statement yet must return different values, and the update carries a live timestamp literal —
    so tests drive `exec` positionally instead of through the statement registry."""
    mock_session.exec = mock.AsyncMock(side_effect=list(results))


def batch_of(*outcomes: DeliveryOutcome) -> BatchResult:
    """Wrap resolved outcomes as a non-flood BatchResult — the shape record_batch_outcomes takes."""
    return BatchResult(outcomes=list(outcomes), flood_control=False)
