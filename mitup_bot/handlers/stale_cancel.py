from enum import auto

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.callback_data import CallbackData
from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.utils.messages import CommonMessages
from mitup_bot.utils.mitup_types import TMitupContext

from .registry import HandlersRegistry

# Matches every cancel-action callback regardless of entity, so stale cancel
# buttons from any conversation (create, edit title, location, duration, etc.)
# are caught when no active conversation claims them first.
STALE_CANCEL = CallbackData(action="cancel", entity="[^:]+")


class StaleCancelHandlerId(HandlerId):
    STALE_CANCEL_CALLBACK = auto()


@HandlersRegistry.register_callback_query(
    handler_id=StaleCancelHandlerId.STALE_CANCEL_CALLBACK,
    callback_data=STALE_CANCEL,
    auto_answer=False,
)
@with_session
async def callback_query_stale_cancel(session: AsyncSession, update: Update, context: TMitupContext):
    # Only reads `user.lang` for the alert text; never traverses the meetups/joined_links collections.
    user = await guards.current_user(update, session, load_collections=False)

    alert_text = CommonMessages.STALE_CANCEL_ALERT.get(lang=user.lang)
    await context.api.answer_callback_query(update, text=alert_text, show_alert=True)

    await context.api.clear_reply_markup(update)
