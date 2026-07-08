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
status once the send completes: SENT / SKIPPED_INACTIVE / FAILED (terminal), or RETRY_PENDING for
a transient failure. A crash between claiming a batch and recording its outcomes leaves the
claimed rows stuck IN_PROGRESS forever: they are never resent (only PENDING and due RETRY_PENDING
rows are ever claimable) and never silently counted as a terminal outcome. `finalize_broadcast`
counts any IN_PROGRESS rows still present at finalization as orphans — genuinely unknown outcomes,
kept separate from FAILED and never retried. No recipient is ever messaged twice, whether the
threat is a crash or a second live worker.

Transient-failure retries — a FAILED delivery means the Telegram call genuinely errored (the
message was NOT delivered), so retrying it is double-send-safe; retrying an IN_PROGRESS orphan is
NOT, because its outcome is unknown and possibly a success. That is why only PENDING and due
RETRY_PENDING rows are ever re-claimed, never IN_PROGRESS. A retryable failure (flood control, or
an unexpected error) parks the delivery in RETRY_PENDING with a `next_attempt_time`; the claim
re-selects it once that time passes, bumping `attempt_count` atomically, until it succeeds or
crosses `MAX_DELIVERY_ATTEMPTS` and lands FAILED. FAILED is strictly permanent (a genuine
per-recipient error or the exhausted retry cap). The sender's traffic runs through a dedicated bot
instance with a low proactive per-second rate cap (`bot.broadcast_max_rate`, wired in
`recurrent_events.build_broadcast_bot`) so broadcasts never crowd time-sensitive events off the
shared limiter — which makes a reactive `RetryAfter` a last-resort signal rather than routine.
Flood control (`RetryAfter`) is special: it halts
the batch the instant it fires — the triggering row keeps its incremented attempt (a real call
happened), but the untried remainder of the batch is released back to RETRY_PENDING with its claim
increment undone (no attempt was spent) and `next_attempt_time` set to the flood backoff, and the
run's drain loop stops so the next tick resumes after the window. While any delivery is still
PENDING/RETRY_PENDING the broadcast is NOT finalized and its `Broadcast.attempts` counter is reset
to 0 — that counter guards only against a worker crash-loop (a crashing worker never reaches the
clean-exit reset), not against a broadcast legitimately waiting out backoff across many ticks.

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
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from sqlalchemy import Row
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import and_, case, col, delete, func, literal, or_, select, update
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.error import BadRequest, NetworkError, RetryAfter

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
# Times a single delivery is sent before a transient failure at this attempt is failed permanently.
MAX_DELIVERY_ATTEMPTS = 3
# Base backoff for an unexpected send error; doubles with each attempt.
RETRY_BACKOFF_BASE_SECONDS = 60
# Added to Telegram's requested flood-control wait so the retry lands past the window's edge.
RETRY_AFTER_MARGIN_SECONDS = 5
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
    attempt_count: int


@dataclass
class DeliveryClassification:
    """How `deliver_one` classified a single send, before the per-delivery attempt cap is applied.

    A RETRY_PENDING status means the send failed transiently and was not delivered; `retry_delay`
    carries the backoff and `flood_control` marks a Telegram `RetryAfter` (which halts the run).
    """

    status: BroadcastDeliveryStatus
    error: str | None
    retry_delay: dt.timedelta | None = None
    flood_control: bool = False


@dataclass
class DeliveryOutcome:
    delivery_id: int
    user_id: int
    status: BroadcastDeliveryStatus
    next_attempt_time: dt.datetime | None = None


@dataclass
class BatchResult:
    """The resolved outcomes of one delivered batch. `flood_control` tells `send_all_pending` to
    stop claiming further batches this run. When flood control halts the batch mid-way,
    `unattempted` carries the still-IN_PROGRESS rows the loop never sent, to be released back to
    RETRY_PENDING after `flood_backoff` with their claim increment undone."""

    outcomes: list[DeliveryOutcome]
    flood_control: bool
    unattempted: list[PendingDelivery] = field(default_factory=list)
    flood_backoff: dt.timedelta | None = None


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


@db.with_session
async def count_unfinished_deliveries(session: AsyncSession, broadcast_id: int) -> int:
    statement = (
        select(func.count())
        .select_from(BroadcastDelivery)
        .where(
            and_(
                col(BroadcastDelivery.broadcast_id) == broadcast_id,
                col(BroadcastDelivery.status).in_(
                    [BroadcastDeliveryStatus.PENDING, BroadcastDeliveryStatus.RETRY_PENDING]
                ),
            )
        )
    )
    return (await session.exec(statement)).one()


@db.with_session
async def reset_broadcast_attempts(session: AsyncSession, broadcast_id: int):
    await session.exec(update(Broadcast).where(col(Broadcast.id) == broadcast_id).values(attempts=0))


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


@db.with_session
async def claim_pending_batch(session: AsyncSession, broadcast_id: int) -> list[PendingDelivery]:
    """Atomically claim up to `BROADCAST_BATCH_SIZE` due deliveries for this worker alone.

    Eligible rows are PENDING, or RETRY_PENDING whose `next_attempt_time` has passed. See the
    module docstring: the `FOR UPDATE SKIP LOCKED` subquery, the status flip and the
    `attempt_count` bump all happen in one statement, so a concurrent worker's claim can never
    match a row this one already took — it just skips locked rows and claims whatever is left.
    Claimed rows land on IN_PROGRESS until `record_batch_outcomes` resolves them; a crash before
    that leaves them IN_PROGRESS and they are counted as orphans at finalization.
    """
    now = dt.datetime.now(dt.UTC)
    claim_ids = (
        select(col(BroadcastDelivery.id))
        .where(
            and_(
                col(BroadcastDelivery.broadcast_id) == broadcast_id,
                or_(
                    col(BroadcastDelivery.status) == BroadcastDeliveryStatus.PENDING,
                    and_(
                        col(BroadcastDelivery.status) == BroadcastDeliveryStatus.RETRY_PENDING,
                        col(BroadcastDelivery.next_attempt_time) <= now,
                    ),
                ),
            )
        )
        .order_by(col(BroadcastDelivery.id))
        .limit(BROADCAST_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    claim_statement = (
        update(BroadcastDelivery)
        .where(col(BroadcastDelivery.id).in_(claim_ids))
        .values(
            status=BroadcastDeliveryStatus.IN_PROGRESS,
            attempt_count=col(BroadcastDelivery.attempt_count) + 1,
            next_attempt_time=None,
        )
        .returning(
            col(BroadcastDelivery.id),
            col(BroadcastDelivery.user_id),
            col(BroadcastDelivery.language_sent),
            col(BroadcastDelivery.attempt_count),
        )
    )
    claimed = (await session.exec(claim_statement)).all()
    if not claimed:
        return []

    return await resolve_claimed_recipients(session, claimed)


async def resolve_claimed_recipients(session: AsyncSession, claimed: Sequence[Row[Any]]) -> list[PendingDelivery]:
    """Load the full `User` row for each just-claimed delivery — `send_message_to_user` sends
    through the ORM object, not a bare tg id. Read-only: no lock is needed, the claim already
    secured exclusive ownership of the delivery rows themselves.

    Postgres does not preserve the claim subquery's `ORDER BY id` in the UPDATE's RETURNING output
    (RETURNING follows the update's scan order, which is unspecified), so the rows are re-sorted by
    delivery id here. Batch send order must follow delivery id: it keeps the lowest-id row the
    flood trigger so its attempt count grows monotonically, and makes a flood halt release the
    highest-id remainder deterministically.
    """
    claimed = sorted(claimed, key=lambda row: cast(int, row[0]))
    user_ids = [cast(int, user_id) for _, user_id, _, _ in claimed]
    users_by_id = {
        user.db_id: user for user in (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()
    }
    return [
        PendingDelivery(
            delivery_id=cast(int, delivery_id),
            user=users_by_id[cast(int, user_id)],
            language_sent=language,
            attempt_count=cast(int, attempt_count),
        )
        for delivery_id, user_id, language, attempt_count in claimed
    ]


async def deliver_batch(
    api: TelegramApiWrapper, broadcast_id: int, batch: list[PendingDelivery], bodies: dict[str, str]
) -> BatchResult:
    """Deliver the batch in order, stopping the moment Telegram flood control fires. The
    triggering row's outcome is kept (a real API call happened, so its incremented attempt
    stands); the untried remainder is carried out for release, since hammering more sends into a
    known flood window would burn their capped attempts on non-attempts."""
    outcomes: list[DeliveryOutcome] = []
    for index, pending in enumerate(batch):
        classification = await deliver_one(api, pending.user, bodies[pending.language_sent], pending.attempt_count)
        outcome = resolve_delivery_outcome(pending, classification)
        log_delivery(broadcast_id, pending, outcome, classification)
        outcomes.append(outcome)
        if classification.flood_control:
            return BatchResult(
                outcomes=outcomes,
                flood_control=True,
                unattempted=batch[index + 1 :],
                flood_backoff=classification.retry_delay,
            )
    return BatchResult(outcomes=outcomes, flood_control=False)


async def deliver_one(
    api: TelegramApiWrapper, user: User, body_html: str, attempt_count: int
) -> DeliveryClassification:
    """Send one body and classify the outcome.

    The stored `body_html` is converted to a `FormattedText` via `parse_format_tags` — the same
    rendering the preview uses, so preview and delivery are guaranteed to match — and sent
    through `send_message_to_user`, which preserves the parsed entities and already classifies a
    blocked/deleted recipient as `InactiveUserInteraction`. Flood control (`RetryAfter`) and any
    unexpected error are transient and retryable (RETRY_PENDING with a backoff); a `BadRequest` is
    a permanent per-recipient failure; a `NetworkError` is systemic and re-raised to abort the run
    (a `TimedOut` may actually have delivered, so it can never be a retry — it stays orphan
    territory). None of these, except `NetworkError`, stops the fan-out.
    """
    formatted_body = parse_format_tags(body_html, {})
    try:
        await api.send_message_to_user(user, formatted_body)
    except InactiveUserInteraction:
        return DeliveryClassification(BroadcastDeliveryStatus.SKIPPED_INACTIVE, "bot blocked by user")
    except RetryAfter as error:
        delay = flood_control_backoff(error.retry_after)
        return DeliveryClassification(
            BroadcastDeliveryStatus.RETRY_PENDING, error.message, retry_delay=delay, flood_control=True
        )
    except BadRequest as error:
        return DeliveryClassification(BroadcastDeliveryStatus.FAILED, error.message)
    except NetworkError:
        raise
    except Exception as error:
        delay = dt.timedelta(seconds=RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt_count - 1))
        return DeliveryClassification(BroadcastDeliveryStatus.RETRY_PENDING, str(error), retry_delay=delay)
    return DeliveryClassification(BroadcastDeliveryStatus.SENT, None)


def flood_control_backoff(retry_after: int | dt.timedelta) -> dt.timedelta:
    """Telegram's requested flood-control wait plus a margin so the retry lands past the window.
    `RetryAfter.retry_after` is seconds or a timedelta depending on PTB's configuration."""
    window = retry_after if isinstance(retry_after, dt.timedelta) else dt.timedelta(seconds=retry_after)
    return window + dt.timedelta(seconds=RETRY_AFTER_MARGIN_SECONDS)


def resolve_delivery_outcome(pending: PendingDelivery, classification: DeliveryClassification) -> DeliveryOutcome:
    """Apply the per-delivery attempt cap: a transient failure at or past `MAX_DELIVERY_ATTEMPTS`
    becomes a permanent FAILED, otherwise it is scheduled for retry after its backoff."""
    if classification.status is not BroadcastDeliveryStatus.RETRY_PENDING:
        return DeliveryOutcome(pending.delivery_id, pending.user.db_id, classification.status)
    if pending.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        return DeliveryOutcome(pending.delivery_id, pending.user.db_id, BroadcastDeliveryStatus.FAILED)
    assert classification.retry_delay is not None, "A RETRY_PENDING classification always carries a backoff"
    next_attempt_time = dt.datetime.now(dt.UTC) + classification.retry_delay
    return DeliveryOutcome(
        pending.delivery_id, pending.user.db_id, BroadcastDeliveryStatus.RETRY_PENDING, next_attempt_time
    )


def log_delivery(
    broadcast_id: int, pending: PendingDelivery, outcome: DeliveryOutcome, classification: DeliveryClassification
):
    emit = log.info if outcome.status is BroadcastDeliveryStatus.SENT else log.warning
    retry_in = None
    if outcome.status is BroadcastDeliveryStatus.RETRY_PENDING and classification.retry_delay is not None:
        retry_in = round(classification.retry_delay.total_seconds())
    emit(
        "broadcast_delivery",
        broadcast_id=broadcast_id,
        tg_user_id=pending.user.tg_user_id,
        lang=pending.language_sent,
        outcome=outcome.status.value,
        attempt=pending.attempt_count,
        retry_in=retry_in,
        error=classification.error,
    )


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
