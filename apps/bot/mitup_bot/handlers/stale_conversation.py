import structlog
from telegram import Update

from mitup_bot import guards
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils.messages import CommonMessages
from mitup_bot.views import RenderContext, factory

from .error_handler import stored_lang, unregistered_caller_lang

log = structlog.get_logger(__name__)

STALE_CONVERSATION_EVENT = "Answered a stale conversation button"


async def answer_stale_conversation_button(context: TMitupContext, update: Update):
    """Take over a prompt whose conversation is gone, leaving the caller on the main menu.

    The tapped button still delivers its query, but only a live conversation state can act on it,
    so the prompt it sits on is replaced by the main menu with a note saying why: that is what
    takes the unusable buttons off the screen. Conversation prompts only ever live in the bot's
    own chat, so the message is always ours to replace.

    Delivery is best-effort: the tapped message may be gone or no longer editable, and an
    exception raised here would put the invocation on the fault path this answer exists to
    keep it off.
    """
    try:
        lang = await stored_lang(update) or unregistered_caller_lang(update)
        view = factory.main_menu_view(
            RenderContext(lang=lang, is_admin=guards.is_admin(update, context)),
            message=CommonMessages.STALE_BUTTONS_NOTICE.get(lang=lang),
        )
        await context.api.answer_callback_query(update=update, text="", show_alert=False)
        await context.api.edit_message(update=update, view=view)
    except Exception:
        log.warning(
            "Failed to deliver the stale-buttons notice to the user",
            exc_info=True,
            reason="stale_buttons_notice_undeliverable",
        )
        return

    log.info(STALE_CONVERSATION_EVENT)
