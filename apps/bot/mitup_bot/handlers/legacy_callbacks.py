import json

import structlog
from telegram import Update

from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages
from mitup_bot.views import MitupView

from .error_handler import in_bot_chat, stored_lang, unregistered_caller_lang

log = structlog.get_logger(__name__)

LEGACY_CALLBACK_EVENT = "Answered a legacy bot callback"

# The two shapes the bot Mitup replaced put on a button. Every one of its buttons carried a JSON
# object of exactly `{"c": <controller shortname>, "d": "<action>:<data>"}`; the blank cells of its
# calendar carried the bare string below instead.
LEGACY_CALLBACK_KEYS = frozenset({"c", "d"})
LEGACY_BLANK_CELL = "nothing"


def is_legacy_callback_data(data: str | None) -> bool:
    """Whether `data` came off a button rendered by the bot this one replaced.

    Neither legacy shape is reachable from `CallbackData`, whose wire format is
    `{action};{entity}:{id}`, so recognising one is unambiguous. The key set is matched exactly:
    anything else, JSON or not, is data no button of ours minted, and stays a fault.
    """
    if data is None:
        return False
    if data == LEGACY_BLANK_CELL:
        return True
    try:
        parsed = json.loads(data)
    except ValueError:
        return False
    return (
        isinstance(parsed, dict)
        and parsed.keys() == LEGACY_CALLBACK_KEYS
        and all(isinstance(value, str) for value in parsed.values())
    )


async def legacy_caller_lang(update: Update) -> str:
    """The language to answer a legacy tap in.

    The old bot's users were migrated with their accounts, so most of them still have a row carrying
    the language they picked; the client tag is what is left for the few who do not.
    """
    return await stored_lang(update) or unregistered_caller_lang(update)


def legacy_notice_view(lang: str) -> MitupView:
    """The screen that replaces a tapped legacy message.

    It is the whole screen the user is left looking at, and every button it replaces is dead, so the
    main menu is the only way on from here.
    """
    return MitupView(description=CommonMessages.OLD_VERSION_MESSAGE.get(lang=lang), keyboard=[]).with_back_button(
        ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU
    )


async def deliver_legacy_notice(context: TMitupContext, update: Update):
    """Tell the caller the message they tapped predates this bot, in the shape its chat can carry.

    In the bot's own chat the dead message is ours to replace, and the notice takes it over. The old
    bot could also leave cards in group chats, where the message belongs to a conversation that is
    not ours: there the notice is an alert only the caller sees and the card is left untouched.

    Either way the query is answered, which is what clears the pressed button's spinner on the
    client; the replacement path answers with no text because the screen underneath carries the whole
    message.
    """
    lang = await legacy_caller_lang(update)
    if in_bot_chat(update):
        await context.api.answer_callback_query(update=update, text="", show_alert=False)
        await context.api.edit_message(update=update, view=legacy_notice_view(lang))
        return

    await context.api.answer_callback_query(
        update=update, text=CommonMessages.OLD_VERSION_MESSAGE.get_text(lang=lang), show_alert=True
    )


async def answer_legacy_callback(context: TMitupContext, update: Update):
    """Answer a tap on a button the previous bot rendered, closing the invocation as a success.

    The message carrying that button is years old and its owner has no way of knowing it is dead, so
    the tap is an interaction to answer rather than a failure to record.

    Delivery is best-effort, like the renders in the error handler: the tapped message may be gone or
    no longer editable, and an exception raised here would put the invocation back on the fault path
    this branch exists to keep it off.
    """
    try:
        await deliver_legacy_notice(context, update)
    except Exception:
        log.warning(
            "Failed to deliver the old-version notice to the user",
            exc_info=True,
            reason="old_version_notice_undeliverable",
        )
        return

    log.info(LEGACY_CALLBACK_EVENT)
