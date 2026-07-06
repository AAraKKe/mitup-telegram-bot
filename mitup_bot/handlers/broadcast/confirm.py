from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Broadcast
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import BroadcastHandlerId


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, callback_data=cb.CONFIRM_BROADCAST, bindable=False
)
@with_session
async def callback_query_confirm_broadcast(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    broadcast_id = guards.valid_callback_data(
        cb.CONFIRM_BROADCAST.parse(context.match), BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK
    ).id
    operator = await guards.current_user(update, session)

    broadcast = await load_draft(session, broadcast_id, operator.tg_user_id)
    if broadcast is None:
        await context.api.edit_message(
            update=update, view=BroadcastOperatorMessages.DRAFT_NOT_FOUND.get(lang=operator.lang)
        )
        return ConversationHandler.END

    broadcast.status = BroadcastStatus.QUEUED
    await context.api.edit_message(
        update=update, view=BroadcastOperatorMessages.QUEUED_CONFIRMATION.get(lang=operator.lang, name=broadcast.name)
    )
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, callback_data=cb.CANCEL_BROADCAST, bindable=False
)
@with_session
async def callback_query_cancel_broadcast(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    broadcast_id = guards.valid_callback_data(
        cb.CANCEL_BROADCAST.parse(context.match), BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK
    ).id
    operator = await guards.current_user(update, session)

    if (broadcast := await load_draft(session, broadcast_id, operator.tg_user_id)) is not None:
        await session.delete(broadcast)
    await context.api.edit_message(
        update=update, view=BroadcastOperatorMessages.CANCELLED_CONFIRMATION.get(lang=operator.lang)
    )
    return ConversationHandler.END


async def load_draft(session: AsyncSession, broadcast_id: int, author_tg_id: int) -> Broadcast | None:
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None or broadcast.author_tg_id != author_tg_id or broadcast.status is not BroadcastStatus.DRAFT:
        return None
    return broadcast
