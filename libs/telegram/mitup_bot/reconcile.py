"""Model-aware reconcile behavior for the db write lifecycle.

The db layer owns the lifecycle ordering (capture, commit, drain, reconcile) but stays
ignorant of both the api implementation and the models the fix-ups touch; this module
supplies that knowledge and is registered onto the db layer at process startup.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy import case
from sqlmodel import col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import Message, User
from mitup_bot.models.users import InactiveReason
from mitup_bot.protocols import ContextOrBotAdapter

log = structlog.get_logger(__name__)


async def advance_render_digests(session: AsyncSession, digests: Mapping[int, str]):
    """Stamp each card with the digest of the payload Telegram confirmed for it, so the next
    render of an unchanged card can skip its edit.

    One statement carries the whole batch: the `CASE` picks each row's own digest, which keeps a
    fan-out over a widely-shared meeting at a single round trip to Postgres.
    """
    if not digests:
        return
    await session.exec(  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        update(Message)
        .where(col(Message.id).in_(list(digests)))
        .values(render_digest=case(digests, value=col(Message.id)))
    )


async def reconcile_outbox(session: AsyncSession, _adapter: ContextOrBotAdapter, outbox: db.OutboxProtocol):
    """Apply the DB fix-ups discovered while draining a write-mode outbox: drop Message rows
    Telegram reported gone, advance the digests of the cards it confirmed, and mark unreachable
    users inactive."""
    if outbox.dead_message_ids:
        log.info("Deleting messages reported gone during fan-out", message_ids=outbox.dead_message_ids)
        await session.exec(  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            delete(Message).where(col(Message.id).in_(outbox.dead_message_ids))
        )
    await advance_render_digests(session, outbox.confirmed_render_digests)
    for tg_user_id in dict.fromkeys(outbox.inactive_tg_user_ids):
        user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
        if user is not None:
            user.mark_inactive(InactiveReason.POST_COMMIT_FANOUT_UNREACHABLE)


def register_outbox_reconciler():
    """Wire the reconcile behavior into the db write lifecycle; every process entry point
    that runs write-mode critical sections calls this once at startup."""
    db.set_outbox_reconciler(reconcile_outbox)
    # `begin_write` asserts on this wiring at the first write-mode critical section; the breadcrumb
    # turns that far-away assertion into a startup fact.
    log.info("Registered the outbox reconciler")
