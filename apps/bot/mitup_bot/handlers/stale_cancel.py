from enum import auto

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.callback_data import CallbackData
from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils.messages import CommonMessages
from mitup_bot.views import MitupView

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
    # Only reads `user.lang` to render in; never traverses the meetups/joined_links collections.
    user = await guards.current_user(update, session)

    alert_text = CommonMessages.STALE_CANCEL_ALERT.text(lang=user.lang)
    await context.api.answer_callback_query(update, text=alert_text, show_alert=True)

    # Replacing the screen is what takes its buttons away: a message renders exactly the buttons
    # its content names, and a keyboard-less view names none.
    expired = MitupView(message=CommonMessages.STALE_CANCEL_ALERT.rich(lang=user.lang), menu=[])
    await context.api.edit_message(update, expired)
