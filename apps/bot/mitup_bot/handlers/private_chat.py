from enum import StrEnum, auto

import structlog
from telegram import Update

from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils.messages import CommonMessages

from .error_handler import stored_lang, unregistered_caller_lang

log = structlog.get_logger(__name__)

OUTSIDE_PRIVATE_CHAT_EVENT = "Rejected interaction from outside the private chat"


class OutsidePrivateChat(StrEnum):
    """Where a refused callback query came from instead of the caller's own chat with the bot."""

    SHARED_CHAT = auto()
    INLINE_MESSAGE = auto()


async def answer_outside_private_chat(context: TMitupContext, update: Update):
    """Tell the caller the bot is not meant to be used from a group, and touch nothing else.

    The message the button sits on belongs to a conversation that is not the bot's, so the answer
    is an alert only the caller sees and the message is left standing. Delivery is best-effort: an
    exception raised here would put the invocation on the fault path this answer exists to keep it
    off.
    """
    reason = OutsidePrivateChat.SHARED_CHAT if update.effective_chat else OutsidePrivateChat.INLINE_MESSAGE
    log.info(OUTSIDE_PRIVATE_CHAT_EVENT, reason=reason.value)

    try:
        lang = await stored_lang(update) or unregistered_caller_lang(update)
        await context.api.answer_callback_query(
            update=update,
            text=CommonMessages.PRIVATE_CHAT_ONLY_ALERT.text(lang=lang),
            show_alert=True,
        )
    except Exception:
        log.warning(
            "Failed to deliver the private-chat alert to the user",
            exc_info=True,
            reason="private_chat_alert_undeliverable",
        )
