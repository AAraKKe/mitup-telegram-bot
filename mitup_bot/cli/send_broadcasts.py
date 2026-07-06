"""Sender phase of the mass-broadcast feature: the background fan-out that drains one
queued broadcast per run inside the recurrent-events service.

Send mechanism — immediate mode, not the capture/`begin_write` outbox. The operator's hard
requirement is one structured log line per delivery attempt (`broadcast_delivery`), which the
outbox cannot provide: under capture the sends drain later inside the api layer, and capture
mode rejects the per-result callbacks that would let us observe each outcome. So each delivery
is sent synchronously and its outcome recorded right after.

Concurrent-worker guarantee — the recurrent-events service runs at `desired_count=1` in steady
state, but an ECS rolling deploy briefly runs the old and new task together, each ticking its own
60s loop independently. `claim_next_broadcast`'s row lock only covers the short claim
transaction, so BOTH tasks can end up believing they own the same `SENDING` broadcast at once —
the real guarantee against double-sending lives one level down, in `claim_pending_batch`: it
atomically flips up to `BROADCAST_BATCH_SIZE` PENDING deliveries via
`UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING ...`. Because the row lock
and the status flip happen in the same statement, at most one worker's UPDATE can ever match a
given delivery row — a second worker's concurrent claim simply skips locked rows and picks
whatever remains (or nothing). No two workers can ever fetch, let alone send, the same delivery.

The claim flips rows to IN_PROGRESS — an honest "claimed, outcome unknown yet" state, not a
guess at the real outcome — and `record_batch_outcomes` resolves each claimed row to its real
terminal status (SENT, SKIPPED_INACTIVE, or FAILED) once the send completes. A crash between
claiming a batch and recording its outcomes leaves the claimed rows stuck IN_PROGRESS forever:
they are never resent (only PENDING rows are ever claimable) and never silently counted as a
terminal outcome. `finalize_broadcast` counts any IN_PROGRESS rows still present at finalization
as orphans — genuinely unknown outcomes, kept separate from FAILED so a future retry mechanism
keyed off FAILED can't be misled into re-sending or skipping them. No recipient is ever messaged
twice, whether the threat is a crash or a second live worker.

Because two workers can each drain a disjoint slice of the same broadcast's deliveries, both can
independently observe "no PENDING rows left" and reach finalization for the same `broadcast_id`.
The count rollup and delivery purge are idempotent either way, but the one-time operator summary
DM is not — `transition_to_terminal` guards it with a compare-and-swap UPDATE on
`status still SENDING`, so only the worker whose UPDATE actually wins the transition notifies.

Durability guarantees: the whole-broadcast anti-resend guarantee is the
`Broadcast.status` transition; resume never re-sends an already-claimed delivery because only
`PENDING` rows are ever eligible for the atomic claim; the audience snapshot is idempotent via
INSERT ... ON CONFLICT DO NOTHING on (broadcast_id, user_id); and final counts are derived by
aggregating the delivery table, never from in-memory accumulators (which reset across resumes).
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import structlog
from sqlalchemy import Row
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import and_, case, col, delete, func, literal, select, update
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.error import BadRequest, NetworkError

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models import Broadcast, BroadcastDelivery, BroadcastMessage, Settings, User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.entities import FormattedText, parse_format_tags
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.views import MitupView

log = structlog.get_logger(__name__)

# Once this many runs have each claimed the broadcast without completing it, declare it FAILED.
MAX_BROADCAST_ATTEMPTS = 5
# Recipients handled per PENDING-query page; also the crash re-send window under immediate mode.
BROADCAST_BATCH_SIZE = 50
# The anonymous-invitee sentinel is never a reachable recipient.
ANONYMOUS_INVITEE_TG_ID = -1
FALLBACK_LANG = TranslationEngine.FALLBACK_LANG


@dataclass
class ClaimedBroadcast:
    broadcast_id: int
    attempts: int
    terminal_failure: bool


@dataclass
class PendingDelivery:
    delivery_id: int
    user: User
    language_sent: str


@dataclass
class DeliveryOutcome:
    delivery_id: int
    user_id: int
    status: BroadcastDeliveryStatus


@dataclass
class LanguageBreakdown:
    language: str
    sent: int
    failed: int
    skipped: int
    orphaned: int = 0


@dataclass
class BroadcastSummary:
    name: str
    status: BroadcastStatus
    attempts: int
    total: int
    sent: int
    failed: int
    skipped: int
    breakdown: list[LanguageBreakdown]
    orphaned: int = 0


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


@db.with_session
async def claim_next_broadcast(session: AsyncSession) -> ClaimedBroadcast | None:
    """Pick the oldest QUEUED or SENDING broadcast, bump its attempt count, and start it.

    QUEUED broadcasts transition to SENDING with a start stamp; SENDING ones are resumed. Once
    the bumped attempt count crosses `MAX_BROADCAST_ATTEMPTS` the broadcast is flagged terminal.

    `FOR UPDATE SKIP LOCKED` here only protects two claims landing in the exact same instant
    (each picks a different broadcast, or one gets none) — it does not stop two long-lived
    workers from both resuming the same `SENDING` broadcast across separate ticks, since this
    transaction's lock is released well before either starts sending. That real guarantee is
    `claim_pending_batch`'s job (see the module docstring); this lock is cheap defense in depth.
    """
    statement = (
        select(Broadcast)
        .where(col(Broadcast.status).in_([BroadcastStatus.QUEUED, BroadcastStatus.SENDING]))
        .order_by(col(Broadcast.id))
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    broadcast = (await session.exec(statement)).first()
    if broadcast is None:
        return None

    broadcast.attempts += 1
    if broadcast.status is BroadcastStatus.QUEUED:
        broadcast.status = BroadcastStatus.SENDING
        broadcast.sending_started_time = dt.datetime.now(dt.UTC)

    return ClaimedBroadcast(
        broadcast_id=broadcast.db_id,
        attempts=broadcast.attempts,
        terminal_failure=broadcast.attempts > MAX_BROADCAST_ATTEMPTS,
    )


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
    await finalize_and_report(api, metrics, admin_tg_ids, claimed.broadcast_id, BroadcastStatus.DONE)


@db.with_session
async def load_broadcast_bodies(session: AsyncSession, broadcast_id: int) -> dict[str, str]:
    """Map each broadcast language to its HTML body, keyed for per-delivery lookup."""
    statement = select(BroadcastMessage).where(col(BroadcastMessage.broadcast_id) == broadcast_id)
    messages = (await session.exec(statement)).all()
    return {message.language: message.body_html for message in messages}


@db.with_session
async def materialize_audience(
    session: AsyncSession, broadcast_id: int, message_languages: list[str]
) -> tuple[int, bool]:
    """Insert one PENDING delivery per reachable MEMBER, resolving each recipient's language.

    Idempotent and resume-safe: if the snapshot already exists the recipient set is frozen and
    the recorded total is returned unchanged, with the second element `False` so the caller
    knows not to re-emit `BROADCAST_MESSAGES_TO_SEND` on every resumed attempt — only the run
    that actually materializes the audience should count it. The insert uses ON CONFLICT DO
    NOTHING on (broadcast_id, user_id) so a crash-and-resume can never duplicate a recipient's row.
    """
    if existing := await count_deliveries(session, broadcast_id):
        return existing, False

    language_sent = case(
        (col(Settings.language).in_(message_languages), col(Settings.language)),
        else_=FALLBACK_LANG,
    )
    source = (
        select(
            literal(broadcast_id),
            col(User.id),
            language_sent,
            literal(BroadcastDeliveryStatus.PENDING.value),
        )
        .join(Settings, col(Settings.user_id) == col(User.id))
        .where(and_(User.status == UserStatus.MEMBER, User.tg_user_id != ANONYMOUS_INVITEE_TG_ID))
    )
    insert_statement = (
        pg_insert(BroadcastDelivery)
        .from_select(["broadcast_id", "user_id", "language_sent", "status"], source)
        .on_conflict_do_nothing(constraint="uq_broadcast_deliveries_broadcast_id_user_id")
    )
    await session.exec(insert_statement)

    total = await count_deliveries(session, broadcast_id)
    broadcast = await session.get(Broadcast, broadcast_id)
    assert broadcast is not None, "The claimed broadcast row must still exist"
    broadcast.total_recipients = total
    return total, True


async def count_deliveries(session: AsyncSession, broadcast_id: int) -> int:
    statement = (
        select(func.count()).select_from(BroadcastDelivery).where(col(BroadcastDelivery.broadcast_id) == broadcast_id)
    )
    return (await session.exec(statement)).one()


async def send_all_pending(api: TelegramApiWrapper, metrics: MetricsClient, broadcast_id: int, bodies: dict[str, str]):
    """Drain every PENDING delivery, one atomically-claimed `BROADCAST_BATCH_SIZE` page at a
    time, until no claim returns any rows."""
    while batch := await claim_pending_batch(broadcast_id):
        outcomes = await deliver_batch(api, broadcast_id, batch, bodies)
        await record_batch_outcomes(outcomes, metrics)


@db.with_session
async def claim_pending_batch(session: AsyncSession, broadcast_id: int) -> list[PendingDelivery]:
    """Atomically claim up to `BROADCAST_BATCH_SIZE` PENDING deliveries for this worker alone.

    See the module docstring: the `FOR UPDATE SKIP LOCKED` subquery and the status flip happen in
    one statement, so a concurrent worker's claim can never match a row this one already took —
    it just skips locked rows and claims whatever is left. Claimed rows land on IN_PROGRESS until
    `record_batch_outcomes` resolves them to their real terminal outcome; a crash before that
    leaves them IN_PROGRESS and they are counted as orphans at finalization.
    """
    claim_ids = (
        select(col(BroadcastDelivery.id))
        .where(
            and_(
                col(BroadcastDelivery.broadcast_id) == broadcast_id,
                col(BroadcastDelivery.status) == BroadcastDeliveryStatus.PENDING,
            )
        )
        .order_by(col(BroadcastDelivery.id))
        .limit(BROADCAST_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    claim_statement = (
        update(BroadcastDelivery)
        .where(col(BroadcastDelivery.id).in_(claim_ids))
        .values(status=BroadcastDeliveryStatus.IN_PROGRESS)
        .returning(col(BroadcastDelivery.id), col(BroadcastDelivery.user_id), col(BroadcastDelivery.language_sent))
    )
    claimed = (await session.exec(claim_statement)).all()
    if not claimed:
        return []

    return await resolve_claimed_recipients(session, claimed)


async def resolve_claimed_recipients(session: AsyncSession, claimed: Sequence[Row[Any]]) -> list[PendingDelivery]:
    """Load the full `User` row for each just-claimed delivery — `send_message_to_user` sends
    through the ORM object, not a bare tg id. Read-only: no lock is needed, the claim already
    secured exclusive ownership of the delivery rows themselves."""
    user_ids = [cast(int, user_id) for _, user_id, _ in claimed]
    users_by_id = {
        user.db_id: user for user in (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()
    }
    return [
        PendingDelivery(
            delivery_id=cast(int, delivery_id),
            user=users_by_id[cast(int, user_id)],
            language_sent=language,
        )
        for delivery_id, user_id, language in claimed
    ]


async def deliver_batch(
    api: TelegramApiWrapper, broadcast_id: int, batch: list[PendingDelivery], bodies: dict[str, str]
) -> list[DeliveryOutcome]:
    outcomes: list[DeliveryOutcome] = []
    for pending in batch:
        status, error = await deliver_one(api, pending.user, bodies[pending.language_sent])
        log_delivery(broadcast_id, pending, status, error)
        outcomes.append(DeliveryOutcome(delivery_id=pending.delivery_id, user_id=pending.user.db_id, status=status))
    return outcomes


async def deliver_one(
    api: TelegramApiWrapper, user: User, body_html: str
) -> tuple[BroadcastDeliveryStatus, str | None]:
    """Send one body and classify the outcome.

    The stored `body_html` is converted to a `FormattedText` via `parse_format_tags` — the same
    rendering the preview uses, so preview and delivery are guaranteed to match — and sent
    through `send_message_to_user`, which preserves the parsed entities and already classifies a
    blocked/deleted recipient as `InactiveUserInteraction`. A `NetworkError` is systemic and
    re-raised to abort the run; every other Telegram error is a per-recipient outcome, never a
    reason to stop the fan-out.
    """
    formatted_body = parse_format_tags(body_html, {})
    try:
        await api.send_message_to_user(user, formatted_body)
    except InactiveUserInteraction:
        return BroadcastDeliveryStatus.SKIPPED_INACTIVE, "bot blocked by user"
    except BadRequest as error:
        return BroadcastDeliveryStatus.FAILED, error.message
    except NetworkError:
        raise
    except Exception as error:
        return BroadcastDeliveryStatus.FAILED, str(error)
    return BroadcastDeliveryStatus.SENT, None


def log_delivery(broadcast_id: int, pending: PendingDelivery, status: BroadcastDeliveryStatus, error: str | None):
    emit = log.info if status is BroadcastDeliveryStatus.SENT else log.warning
    emit(
        "broadcast_delivery",
        broadcast_id=broadcast_id,
        tg_user_id=pending.user.tg_user_id,
        lang=pending.language_sent,
        outcome=status.value,
        error=error,
    )


@db.with_session
async def record_batch_outcomes(session: AsyncSession, outcomes: list[DeliveryOutcome], metrics: MetricsClient):
    """Resolve each claimed (IN_PROGRESS) delivery to its real terminal outcome.

    A skipped recipient is also flipped to LEFT.
    """
    by_status: dict[BroadcastDeliveryStatus, list[DeliveryOutcome]] = defaultdict(list)
    for outcome in outcomes:
        by_status[outcome.status].append(outcome)

    if sent := by_status[BroadcastDeliveryStatus.SENT]:
        await mark_deliveries(session, sent, BroadcastDeliveryStatus.SENT, sent_time=dt.datetime.now(dt.UTC))
    if failed := by_status[BroadcastDeliveryStatus.FAILED]:
        await mark_deliveries(session, failed, BroadcastDeliveryStatus.FAILED)
    if skipped := by_status[BroadcastDeliveryStatus.SKIPPED_INACTIVE]:
        await mark_deliveries(session, skipped, BroadcastDeliveryStatus.SKIPPED_INACTIVE)
        await deactivate_skipped_users(session, skipped, metrics)


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


async def deactivate_skipped_users(session: AsyncSession, skipped: list[DeliveryOutcome], metrics: MetricsClient):
    """Flip unreachable MEMBERs to LEFT via `User.mark_inactive`."""
    user_ids = [outcome.user_id for outcome in skipped]
    users = (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()
    left = sum(user.mark_inactive() for user in users)
    if left:
        log.info("Broadcast marked unreachable recipients inactive", count=left)
        metrics.emit(MetricKey.INACTIVE_USER_SET, left, MetricUnit.COUNT)


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

    Any delivery still PENDING at this point never got attempted — a no-op on the normal drained
    path, and on the terminal-failure path (max attempts exceeded, no draining) it correctly
    records never-attempted recipients as genuine non-deliveries. It is marked FAILED before
    aggregation. Rows left IN_PROGRESS are a different case entirely — claimed by a worker that
    crashed before recording an outcome — and are counted separately as orphans, never as FAILED.

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
    await session.exec(
        update(BroadcastDelivery)
        .where(
            and_(
                col(BroadcastDelivery.broadcast_id) == broadcast_id,
                col(BroadcastDelivery.status) == BroadcastDeliveryStatus.PENDING,
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


async def notify_operators(api: TelegramApiWrapper, admin_tg_ids: list[int], summary: BroadcastSummary):
    operators = await load_operators(admin_tg_ids)
    for tg_id, operator in operators.items():
        view = build_summary_view(summary, operator.lang)
        try:
            await api.send_message_to_user(operator, view)
        except InactiveUserInteraction:
            log.warning("Broadcast operator unreachable for summary", tg_user_id=tg_id)


@db.with_session
async def load_operators(session: AsyncSession, admin_tg_ids: list[int]) -> dict[int, User]:
    operators: dict[int, User] = {}
    for tg_id in admin_tg_ids:
        if operator := await User.by_tg_user_id(session, tg_id):
            operators[tg_id] = operator
            continue
        log.warning("Broadcast operator has no reachable user, skipping summary", tg_user_id=tg_id)
    return operators


def build_summary_view(summary: BroadcastSummary, lang: str) -> MitupView:
    if summary.status is BroadcastStatus.FAILED:
        text = BroadcastOperatorMessages.SENDER_FAILED.get(
            lang=lang,
            name=summary.name,
            attempts=summary.attempts,
            sent=summary.sent,
            failed=summary.failed,
            skipped=summary.skipped,
        )
        if summary.orphaned:
            text = FormattedText.join(
                "\n\n",
                [text, BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang=lang, orphaned=summary.orphaned)],
            )
        return MitupView(text, keyboard=[])

    breakdown = FormattedText.join(
        "\n",
        [build_breakdown_line(line, lang) for line in summary.breakdown],
    )
    text = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang=lang,
        name=summary.name,
        total=summary.total,
        sent=summary.sent,
        failed=summary.failed,
        skipped=summary.skipped,
        breakdown=breakdown,
    )
    if summary.orphaned:
        text = FormattedText.join(
            "\n\n",
            [text, BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang=lang, orphaned=summary.orphaned)],
        )
    return MitupView(text, keyboard=[])


def build_breakdown_line(line: LanguageBreakdown, lang: str) -> FormattedText:
    if line.orphaned:
        return BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE_WITH_ORPHANED.get(
            lang=lang,
            language=line.language,
            sent=line.sent,
            failed=line.failed,
            skipped=line.skipped,
            orphaned=line.orphaned,
        )
    return BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
        lang=lang, language=line.language, sent=line.sent, failed=line.failed, skipped=line.skipped
    )
