"""Claim phase: pick the next broadcast, snapshot its audience, and atomically claim due
deliveries. The atomic `claim_pending_batch` is the real anti-double-send guarantee — see the
package docstring in `__init__.py`."""

import datetime as dt
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Row
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import and_, case, col, func, literal, or_, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import Broadcast, BroadcastDelivery, BroadcastMessage, Settings, User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.models.users import UserStatus

from .types import (
    ANONYMOUS_INVITEE_TG_ID,
    BROADCAST_BATCH_SIZE,
    FALLBACK_LANG,
    MAX_BROADCAST_ATTEMPTS,
    ClaimedBroadcast,
    PendingDelivery,
)


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
        author_tg_id=broadcast.author_tg_id,
        attempts=broadcast.attempts,
        terminal_failure=broadcast.attempts > MAX_BROADCAST_ATTEMPTS,
    )


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
async def materialize_audience(session: AsyncSession, broadcast_id: int, message_languages: list[str]) -> int:
    """Insert one PENDING delivery per reachable MEMBER, resolving each recipient's language, and
    return the total recipient count.

    Idempotent and resume-safe: if the snapshot already exists the recipient set is frozen and the
    recorded total is returned unchanged. The insert uses ON CONFLICT DO NOTHING on
    (broadcast_id, user_id) so a crash-and-resume can never duplicate a recipient's row.
    """
    if existing := await count_deliveries(session, broadcast_id):
        return existing

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
    return total


async def count_deliveries(session: AsyncSession, broadcast_id: int) -> int:
    statement = (
        select(func.count()).select_from(BroadcastDelivery).where(col(BroadcastDelivery.broadcast_id) == broadcast_id)
    )
    return (await session.exec(statement)).one()


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
