from enum import auto

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Chat, Update
from telegram.constants import ChatMemberStatus

from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import InactiveReason

from .registry import HandlersRegistry

log = structlog.get_logger(__name__)


class ChatMemberHandlerId(HandlerId):
    MY_CHAT_MEMBER = auto()


def block_status_change(update: Update) -> tuple[str, str] | None:
    """The bot's `(old, new)` membership status in this update, or `None` when it carries none.

    `my_chat_member` describes the **bot's** membership in the chat, so a user blocking the bot
    reads as the bot moving to BANNED. The pair is what the block line reports, so a widened
    `is_block_transition` keeps naming the transition it actually acted on.
    """
    if (chat_member_update := update.my_chat_member) is None:
        return None
    if (status_change := chat_member_update.difference().get("status")) is None:
        return None
    old_status, new_status = status_change
    return str(old_status), str(new_status)


def is_block_transition(update: Update) -> bool:
    """Return ``True`` only for a genuine MEMBER → BANNED transition in a private chat.

    Telegram emits a ``my_chat_member`` update whenever the bot's membership status
    changes. We only care about a user blocking the bot from a DM, which surfaces as
    the bot moving from ``MEMBER`` to ``BANNED`` (the wire-level ``kicked``) in a
    ``private`` chat. Every other transition (unblock, group changes, no status change)
    is ignored.
    """
    if (chat_member_update := update.my_chat_member) is None:
        return False

    if chat_member_update.chat.type != Chat.PRIVATE:
        return False

    difference = chat_member_update.difference()
    if (status_change := difference.get("status")) is None:
        return False

    old_status, new_status = status_change
    return old_status == ChatMemberStatus.MEMBER and new_status == ChatMemberStatus.BANNED


@HandlersRegistry.register_chat_member(handler_id=ChatMemberHandlerId.MY_CHAT_MEMBER)
@with_session
async def chat_member_block_handler(session: AsyncSession, update: Update, context: TMitupContext):
    # Unblock (BANNED/LEFT → MEMBER) is intentionally NOT handled here. The end of the
    # registration conversation is the only legitimate transition back to MEMBER, and the
    # /start re-onboarding flow treats a LEFT user as a re-onboarding case. Restoring the
    # user on unblock would bypass that invariant, so an unblocked user simply re-onboards
    # via /start.
    if not is_block_transition(update):
        return

    # my_chat_member updates always carry an effective_user, but guard explicitly rather than
    # asserting: assertions are stripped under `python -O`, so a malformed update must not
    # cause an AttributeError in production.
    if update.effective_user is None:
        log.warning("Received a my_chat_member update without an effective_user", update=update)
        return

    tg_user_id = update.effective_user.id
    user = await User.by_tg_user_id(session, tg_user_id)
    # The block itself, separately from the demotion it may cause: a block by a user who is already
    # LEFT — or who has no row at all — moves no status and would otherwise leave no trace.
    demoted = user is not None and user.mark_inactive(InactiveReason.BLOCKED_BOT)
    old_status, new_status = block_status_change(update) or ("unknown", "unknown")
    log.info(
        "Bot blocked by user",
        tg_user_id=tg_user_id,
        old_bot_status=old_status,
        new_bot_status=new_status,
        user_found=user is not None,
        demoted=demoted,
    )
