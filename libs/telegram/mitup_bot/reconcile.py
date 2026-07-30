"""Model-aware reconcile behavior for the db write lifecycle.

The db layer owns the lifecycle ordering (capture, commit, drain, reconcile) but stays
ignorant of both the api implementation and the models the fix-ups touch; this module
supplies that knowledge and is registered onto the db layer at process startup.
"""

import structlog
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import Message, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.protocols import ContextOrBotAdapter

log = structlog.get_logger(__name__)


async def reconcile_outbox(session: AsyncSession, adapter: ContextOrBotAdapter, outbox: db.OutboxProtocol):
    """Apply the DB fix-ups discovered while draining a write-mode outbox: drop Message rows
    Telegram reported gone and mark unreachable users inactive."""
    if outbox.dead_message_ids:
        log.info("Deleting messages reported gone during fan-out", message_ids=outbox.dead_message_ids)
        await session.exec(  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            delete(Message).where(col(Message.id).in_(outbox.dead_message_ids))
        )
    for tg_user_id in dict.fromkeys(outbox.inactive_tg_user_ids):
        user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
        if user is not None and user.mark_inactive():
            adapter.emit_metric(MetricKey.INACTIVE_USER_SET)


def register_outbox_reconciler():
    """Wire the reconcile behavior into the db write lifecycle; every process entry point
    that runs write-mode critical sections calls this once at startup."""
    db.set_outbox_reconciler(reconcile_outbox)
    # `begin_write` asserts on this wiring at the first write-mode critical section; the breadcrumb
    # turns that far-away assertion into a startup fact.
    log.info("Registered the outbox reconciler")
