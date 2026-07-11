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

from .enums import BroadcastHandlerId


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
    operator = await guards.current_user(update, session, load_collections=False)

    broadcast = await load_draft(session, broadcast_id, operator.tg_user_id)
    if broadcast is None:
        await context.api.edit_message(
            update=update, view=BroadcastOperatorMessages.DRAFT_NOT_FOUND.get(lang=operator.lang)
        )
        return ConversationHandler.END

    broadcast.status = BroadcastStatus.QUEUED
    await context.api.edit_message(
        update=update,
        view=BroadcastOperatorMessages.QUEUED_CONFIRMATION.get(
            lang=operator.lang, name=broadcast.name, broadcast_id=broadcast.db_id
        ),
    )
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, callback_data=cb.CANCEL_BROADCAST, bindable=False, admin_only=True
)
@with_session
async def callback_query_cancel_broadcast(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    # Reads only `operator.tg_user_id`/`operator.lang`; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session, load_collections=False)

    # The entry-prompt Cancel button carries no id (no draft exists yet), while the preview Cancel
    # button carries the draft id. Parse leniently so both paths land here: only delete when a draft
    # id is present and still resolves to this operator's draft.
    broadcast_id = cb.CANCEL_BROADCAST.parse(context.match).id
    if (
        broadcast_id is not None
        and (broadcast := await load_draft(session, broadcast_id, operator.tg_user_id)) is not None
    ):
        await session.delete(broadcast)

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


async def load_draft(session: AsyncSession, broadcast_id: int, author_tg_id: int) -> Broadcast | None:
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None or broadcast.author_tg_id != author_tg_id or broadcast.status is not BroadcastStatus.DRAFT:
        return None
    return broadcast
