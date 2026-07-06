from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.utils.mitup_types import TMitupContext

from . import utils
from .enums import BroadcastHandlerId, ConversationBroadcastState


@HandlersRegistry.register_command(BroadcastHandlerId.BROADCAST_COMMAND, command="broadcast", bindable=False)
@with_session
async def command_broadcast(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationBroadcastState | int:
    operator = await guards.broadcast_admin(update, session, utils.bot_config(context))
    if operator is None:
        # Not an allowlisted admin: stay silent so the feature never reveals itself.
        return ConversationHandler.END

    await utils.discard_author_drafts(session, operator.tg_user_id)
    await context.api.send_message(update=update, view=BroadcastOperatorMessages.UPLOAD_PROMPT.get(lang=operator.lang))
    return ConversationBroadcastState.AWAITING_CONTENT
