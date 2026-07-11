from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import MitupView

from . import utils
from .enums import BroadcastHandlerId, ConversationBroadcastState


@HandlersRegistry.register_callback_query(
    BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, callback_data=cb.BROADCAST, bindable=False, admin_only=True
)
@with_session
async def callback_query_open_broadcast(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationBroadcastState | int:
    # The registry gate (admin_only) already vetted the admin; member_user only resolves the row
    # for the operator's language. A missing member row (an admin id that is not a registered
    # member) bails silently rather than letting current_user raise UserNotFound.
    operator = await guards.member_user(update, session)
    if operator is None:
        return ConversationHandler.END

    await utils.discard_author_drafts(session, operator.tg_user_id)
    await context.api.edit_message(update=update, view=upload_prompt_view(operator.lang))
    return ConversationBroadcastState.AWAITING_CONTENT


def upload_prompt_view(lang: str) -> MitupView:
    """The upload prompt shown on the admin-menu message, with a Cancel button so the operator is
    never stranded on a keyboard-less message. The Cancel button carries no draft id (none exists
    yet); its `action="cancel"` routes it to the conversation's cancel handler."""
    return MitupView(
        BroadcastOperatorMessages.UPLOAD_PROMPT.get(lang=lang),
        [
            [
                ButtonConfig(
                    text=BroadcastOperatorMessages.BUTTON_CANCEL.get(lang=lang),
                    callback_data=cb.CANCEL_BROADCAST,
                )
            ]
        ],
    )
