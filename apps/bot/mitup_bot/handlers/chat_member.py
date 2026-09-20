from enum import auto

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Chat, ChatMemberUpdated, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

from mitup_bot import guards
from mitup_bot.api_wrapper import chat_member_is_present
from mitup_bot.config import BotConfig
from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import InactiveReason
from mitup_bot.views import factory

from .registry import HandlersRegistry

log = structlog.get_logger(__name__)

# The one membership decision, under one event name: `reason` says why the chat may or may not keep
# the bot, `outcome` says what became of the membership.
MEMBERSHIP_EVENT = "Bot group membership decided"

# The chat types a membership update can report the bot arriving in.
NON_PRIVATE_CHAT_TYPES = frozenset({Chat.GROUP, Chat.SUPERGROUP, Chat.CHANNEL})


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
    changes. A user blocking the bot from a DM surfaces as the bot moving from ``MEMBER``
    to ``BANNED`` (the wire-level ``kicked``) in a ``private`` chat. An unblock and an
    update carrying no status change are both ignored; a group transition belongs to
    ``is_join_transition`` instead.
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


def is_join_transition(membership: ChatMemberUpdated) -> bool:
    """Return ``True`` when the bot has just arrived in a group, supergroup or channel.

    A move between two states that both count as present, a promotion to administrator among them,
    is not an arrival: the decision to stay or leave was taken when the bot first got in.
    """
    if membership.chat.type not in NON_PRIVATE_CHAT_TYPES:
        return False
    return not chat_member_is_present(membership.old_chat_member) and chat_member_is_present(membership.new_chat_member)


def allowed_reason(chat_id: int, bot_config: BotConfig) -> str | None:
    """Why *chat_id* may keep the bot as a member, or ``None`` when it may not."""
    if chat_id == bot_config.hosts_group_chat_id:
        return "hosts_group"
    if chat_id in bot_config.allowed_group_chat_ids:
        return "allowlisted"
    return None


@HandlersRegistry.register_chat_member(handler_id=ChatMemberHandlerId.MY_CHAT_MEMBER)
@with_session
async def my_chat_member_handler(session: AsyncSession, update: Update, context: TMitupContext):
    # Unblock (BANNED/LEFT → MEMBER) is intentionally NOT handled here. The end of the
    # registration conversation is the only legitimate transition back to MEMBER, and the
    # /start re-onboarding flow treats a LEFT user as a re-onboarding case. Restoring the
    # user on unblock would bypass that invariant, so an unblocked user simply re-onboards
    # via /start.
    if is_block_transition(update):
        await record_block(session, update)
        return

    membership = update.my_chat_member
    if membership is not None and is_join_transition(membership):
        await leave_unless_allowed(session, update, membership.chat, context)


async def record_block(session: AsyncSession, update: Update):
    """Mark the user who blocked the bot inactive, and record the block either way."""
    # my_chat_member updates always carry an effective_user, but guard explicitly rather than
    # asserting: assertions are stripped under `python -O`, so a malformed update must not
    # cause an AttributeError in production.
    if update.effective_user is None:
        log.warning("Received a my_chat_member update without an effective_user", update=update)
        return

    tg_user_id = update.effective_user.id
    user = await User.by_tg_user_id(session, tg_user_id)
    # The block itself, separately from the demotion it may cause: a block by a user who is already
    # LEFT, or who has no row at all, moves no status and would otherwise leave no trace.
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


async def leave_unless_allowed(session: AsyncSession, update: Update, chat: Chat, context: TMitupContext):
    """Leave the chat the bot was just added to unless the configuration allows it to stay.

    A group or supergroup is told why on the way out; a channel is not, since a message there is
    published to every subscriber.
    """
    if (reason := allowed_reason(chat.id, context.bot_config)) is not None:
        log.info(MEMBERSHIP_EVENT, chat_type=chat.type, reason=reason, outcome="stayed")
        return

    if chat.type != Chat.CHANNEL:
        await send_farewell(session, update, chat.id, context)

    left = await context.api.leave_chat(chat.id)
    emit = log.info if left else log.warning
    emit(MEMBERSHIP_EVENT, chat_type=chat.type, reason="not_allowed", outcome="left" if left else "leave_failed")


async def send_farewell(session: AsyncSession, update: Update, chat_id: int, context: TMitupContext):
    """Post the farewell in the language of whoever added the bot.

    A group that bars the bot from posting refuses the send, and leaving never waits on it.
    """
    lang = await guards.user_language(update, session)
    try:
        await context.api.send_message_to_chat(chat_id, factory.group_farewell_view(lang))
    except TelegramError:
        log.warning("Group farewell message not delivered", exc_info=True)
