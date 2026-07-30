from enum import StrEnum, auto

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Broadcast
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages

from . import utils
from .enums import BroadcastHandlerId

log = structlog.get_logger(__name__)


class DraftRefusal(StrEnum):
    """Why a draft lookup did not yield a usable draft.

    The three collapse into one operator-facing message, but they are three different situations —
    `AUTHOR_MISMATCH` in particular is one admin acting on another's draft — so the lookup names
    which one it hit instead of answering `None`.
    """

    DRAFT_NOT_FOUND = auto()
    AUTHOR_MISMATCH = auto()
    STATUS_NOT_DRAFT = auto()


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, callback_data=cb.CONFIRM_BROADCAST, bindable=False, admin_only=True
)
@with_session
async def callback_query_confirm_broadcast(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    broadcast_id = guards.valid_callback_data(
        cb.CONFIRM_BROADCAST.parse(context.match), BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK
    ).id
    # Reads only `operator.tg_user_id`/`operator.lang`; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session)

    broadcast = await load_draft(session, broadcast_id, operator.tg_user_id)
    if isinstance(broadcast, DraftRefusal):
        log.warning(
            "Broadcast draft not usable",
            broadcast_id=broadcast_id,
            stage="load_draft",
            outcome="rejected",
            reason=broadcast.value,
        )
        await context.api.edit_message(
            update=update, view=BroadcastOperatorMessages.DRAFT_NOT_FOUND.get(lang=operator.lang)
        )
        return ConversationHandler.END

    previous_status = broadcast.status
    broadcast.status = BroadcastStatus.QUEUED
    await context.api.edit_message(
        update=update,
        view=BroadcastOperatorMessages.QUEUED_CONFIRMATION.get(
            lang=operator.lang, name=broadcast.name, broadcast_id=broadcast.db_id
        ),
    )
    # The authorisation to message every reachable member, and the join point to the sender's run:
    # `updated_time` is the only other trace of it and the sender overwrites that.
    log.info(
        "Broadcast queued",
        broadcast_id=broadcast.db_id,
        broadcast_name=broadcast.name,
        author_tg_id=broadcast.author_tg_id,
        user_id=operator.db_id,
        stage="confirm",
        outcome="queued",
        previous_status=previous_status.value,
        languages=[message.language for message in broadcast.messages],
    )
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, callback_data=cb.CANCEL_BROADCAST, bindable=False, admin_only=True
)
@with_session
async def callback_query_cancel_broadcast(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    # Reads only `operator.tg_user_id`/`operator.lang`; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session)

    # The entry-prompt Cancel button carries no id (no draft exists yet), while the preview Cancel
    # button carries the draft id. Parse leniently so both paths land here: only delete when a draft
    # id is present and still resolves to this operator's draft.
    broadcast_id = cb.CANCEL_BROADCAST.parse(context.match).id
    await discard_cancelled_draft(session, broadcast_id, operator.tg_user_id)

    # Return to the admin menu with the discard confirmation prepended, so the operator both sees
    # that the flow was abandoned and keeps the admin-menu keyboard. Shown unconditionally: whether
    # or not a draft had been persisted yet, the operator did abandon a broadcast flow.
    await context.api.edit_message(
        update=update,
        view=views.factory.admin_menu_view(guards.render_context(operator, update, context)).with_context(
            BroadcastOperatorMessages.CANCELLED_CONFIRMATION.get(lang=operator.lang)
        ),
    )
    return ConversationHandler.END


async def discard_cancelled_draft(session: AsyncSession, broadcast_id: int | None, author_tg_id: int):
    """Delete the draft the Cancel button carried, and record how the flow ended either way.

    An abandoned flow leaves nothing else behind: the entry-prompt Cancel carries no id at all, and
    a stale preview keyboard carries one that no longer resolves.
    """
    if broadcast_id is None:
        log.info("Broadcast flow cancelled", stage="cancel", outcome="no_draft", reason="no_draft_id_in_callback")
        return

    broadcast = await load_draft(session, broadcast_id, author_tg_id)
    if isinstance(broadcast, DraftRefusal):
        log.info(
            "Broadcast flow cancelled",
            broadcast_id=broadcast_id,
            stage="cancel",
            outcome="no_draft",
            reason=broadcast.value,
        )
        return

    utils.log_draft_discarded(broadcast, reason="operator_cancelled")
    await session.delete(broadcast)
    log.info("Broadcast flow cancelled", broadcast_id=broadcast_id, stage="cancel", outcome="draft_discarded")


async def load_draft(session: AsyncSession, broadcast_id: int, author_tg_id: int) -> Broadcast | DraftRefusal:
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        return DraftRefusal.DRAFT_NOT_FOUND
    if broadcast.author_tg_id != author_tg_id:
        return DraftRefusal.AUTHOR_MISMATCH
    if broadcast.status is not BroadcastStatus.DRAFT:
        return DraftRefusal.STATUS_NOT_DRAFT
    return broadcast
