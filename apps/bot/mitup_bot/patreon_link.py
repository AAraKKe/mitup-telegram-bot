"""Binding a Patreon identity, proven in a browser, to a Telegram account proven in a chat.

The OAuth callback can establish *which Patreon account* consented, but it runs in a browser and can
prove nothing about who is holding it — the consent URL is transferable, so anyone can be talked into
completing it. It therefore grants nothing: it parks the proven identity as a pending link and hands
back a pairing code, rendered in the browser that did the consent.

A pairing code is not authority either, for the same reason in reverse: the finish link can be handed
on just as easily, and a tap on it would overwrite a real Host's subscription with a stranger's
account and strip their tier. So the code only opens a prompt, and this module owns the write that
happens after somebody confirms it.

The pending-link row itself — staging, claiming, consuming, erasing — lives in
:mod:`mitup_bot.patreon.pending_links`, which the recurrent-events runner also needs.
"""

import datetime as dt
from dataclasses import dataclass
from enum import Enum, StrEnum, auto

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import hosts_group, supporter
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.db import racy_flush
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import CollaborateMessages, SupporterNotificationMessages
from mitup_bot.views.collaborate import hosts_group_readmitted_view, hosts_group_removed_view, link_confirmation_view

log = structlog.get_logger(__name__)

# Stable machine key bound on every log line of a pairing-code redemption, so the Telegram half of
# the link can be filtered as one series in CloudWatch Logs Insights.
LINK_FLOW = "patreon_account_link"

# A freshly linked patron gets support immediately with a short runway; the daily job
# extends it while the pledge stays active and lets it lapse otherwise.
SUPPORT_GRACE_DAYS = 7

# The unique index that is the real arbiter of "one Patreon account backs one Mitup account". The
# read-side check races, so a violation of this constraint is caught and mapped to the same outcome.
# The name does not track the table's: `rename_table` leaves constraint names alone, so this one
# still carries the table's original name. Read it off the live database, not off the model.
PATREON_USER_ID_UNIQUE_CONSTRAINT = "premium_subscriptions_patreon_user_id_key"


class LinkOutcome(Enum):
    """Result of confirming a pairing code against the confirming account."""

    LINKED_SUPPORTER = auto()
    LINKED_NO_PATRON = auto()
    ALREADY_LINKED_ELSEWHERE = auto()
    PENDING_DELETION = auto()


class RedemptionRefusal(Enum):
    """Why a pairing code could not be turned into a prompt, from the redeemer's point of view."""

    CODE_NOT_USABLE = auto()
    ALREADY_LINKED_ELSEWHERE = auto()
    NOT_A_MEMBER = auto()
    PENDING_DELETION = auto()


class HostsGroupTrigger(StrEnum):
    """Which path asked for a hosts-group readmission. Both reach the same two no-ops, and a
    readmission that did not happen means something different depending on which one it was."""

    LINK = "link"
    WEBHOOK = "webhook"


class LinkKind(StrEnum):
    """Whether a confirmed link created this account's first subscription row or replaced one."""

    FIRST_LINK = "first_link"
    RELINK = "relink"


@dataclass(frozen=True)
class LinkedSubscription:
    """The row a confirmed link wrote, with what it replaced.

    ``previous_patreon_user_id`` is the identity the row carried before, which is the first thing a
    duplicate-entitlement report needs and the only trace of it left after the update.
    """

    subscription: SupporterSubscription
    kind: LinkKind
    previous_patreon_user_id: str | None


async def readmit_to_hosts_group(api: TelegramApiWrapper, user: User, *, trigger: HostsGroupTrigger):
    """Lift any hosts-only group ban so a re-activated host can request to join again.

    Idempotent: ``unban_chat_member(only_if_banned=True)`` is a no-op when the user was never banned.
    The readmission DM is sent only when the user was actually banned, so a first-time linker (who runs
    this path too) is not told they were welcomed back. The banned status is read before the unban,
    since after it the user is no longer banned. A no-op when the feature is unconfigured; the api
    wrapper swallows Telegram failures."""
    chat_id = hosts_group.chat_id()
    if chat_id is None:
        log.info(
            "Skipping hosts-group readmission",
            tg_user_id=user.tg_user_id,
            reason="hosts_group_not_configured",
            trigger=str(trigger),
        )
        return
    was_banned = await api.is_chat_banned(chat_id, user.tg_user_id)
    await api.unban_chat_member(chat_id, user.tg_user_id, only_if_banned=True)
    if not was_banned:
        log.info(
            "Skipping hosts-group readmission",
            tg_user_id=user.tg_user_id,
            chat_id=chat_id,
            reason="user_was_not_banned",
            trigger=str(trigger),
        )
        return
    await api.send_message_to_user(user, hosts_group_readmitted_view(user.lang, hosts_group.invite_url()))
    log.info(
        "Re-admitted host to the hosts-only group",
        tg_user_id=user.tg_user_id,
        chat_id=chat_id,
        trigger=str(trigger),
    )


async def withdraw_from_hosts_group(api: TelegramApiWrapper, user: User):
    """Bring hosts-group state in line with an account that no longer has a Patreon link: out of
    the group, and free of any ban.

    A current member is ejected and then immediately unbanned, so the removal never leaves a
    permanent ban behind. That matters because this runs where the subscription row is deleted:
    without a row the daily sweeps can never nominate this user again, so a ban left here would
    outlive every mechanism able to lift it. Keeping a non-supporter out is the join-request
    gate's job, and it needs no ban to do it.

    The same unban clears a stale ban left by an earlier revoke, which nothing else can reach once
    the row is gone. Group admins are skipped: banChatMember cannot ban the creator and fails on
    other admins. The removal DM goes only to someone who was actually in the group. A no-op when
    the feature is unconfigured; the api wrapper swallows Telegram failures.
    """
    chat_id = hosts_group.chat_id()
    if chat_id is None:
        log.info("Skipping hosts-group withdrawal", tg_user_id=user.tg_user_id, reason="hosts_group_not_configured")
        return
    if await api.is_chat_admin(chat_id, user.tg_user_id):
        log.info(
            "Skipping hosts-group withdrawal",
            tg_user_id=user.tg_user_id,
            chat_id=chat_id,
            reason="user_is_group_admin",
        )
        return
    was_member = await api.is_chat_member(chat_id, user.tg_user_id)
    if was_member:
        await api.ban_chat_member(chat_id, user.tg_user_id)
    await api.unban_chat_member(chat_id, user.tg_user_id, only_if_banned=True)
    if not was_member:
        log.info(
            "Skipping hosts-group withdrawal",
            tg_user_id=user.tg_user_id,
            chat_id=chat_id,
            reason="not_a_group_member",
        )
        return
    await api.send_message_to_user(user, hosts_group_removed_view(user.lang))
    log.info("Removed unlinked user from the hosts-only group", tg_user_id=user.tg_user_id, chat_id=chat_id)


async def upsert_subscription(session: AsyncSession, user: User, patreon_user_id: str) -> LinkedSubscription | None:
    """Point ``user``'s subscription row at ``patreon_user_id``, or None if another account won it.

    A re-link updates the existing row, so a returning user never has to unlink first. Which of the
    two happened, and what the row previously pointed at, is read before the write and returned:
    the update overwrites the only copy of it.

    The read-side "already linked elsewhere" check upstream is a plain SELECT with no lock, so two
    confirmations for one Patreon account can both pass it. The unique index is the real arbiter, and
    ``racy_flush`` is how a loser resolves: the write happens inside a savepoint, a violation of that
    one constraint rolls back cleanly and yields None, and any other integrity error re-raises.
    """
    existing = (
        await session.exec(select(SupporterSubscription).where(SupporterSubscription.user_id == user.db_id))
    ).first()
    kind = LinkKind.FIRST_LINK if existing is None else LinkKind.RELINK
    previous_patreon_user_id = existing.patreon_user_id if existing is not None else None

    def apply_link() -> SupporterSubscription:
        if existing is None:
            return SupporterSubscription(user_id=user.db_id, patreon_user_id=patreon_user_id)
        existing.patreon_user_id = patreon_user_id
        return existing

    subscription = await racy_flush(session, apply_link, constraint=PATREON_USER_ID_UNIQUE_CONSTRAINT)
    if subscription is None:
        return None
    return LinkedSubscription(subscription=subscription, kind=kind, previous_patreon_user_id=previous_patreon_user_id)


async def link_patreon_account(
    session: AsyncSession,
    api: TelegramApiWrapper,
    user: User,
    *,
    patreon_user_id: str,
    granted_level: SupporterLevel,
) -> LinkOutcome:
    """Upsert ``user``'s subscription against the confirmed Patreon identity and confirm it in chat.

    ``user`` is the account that confirmed the prompt, so the Patreon identity can only ever land on
    the account that explicitly asked for it. A re-link updates the existing row, so the user never
    has to unlink first. Runs under the caller's write-mode session: the confirmation DM and the
    hosts-group readmission are queued here and drain once the row is committed.

    ``granted_level`` comes from the pending-link row and is the only authority for what is written;
    the level the user holds right now is never consulted here.
    """
    if user.status is UserStatus.DELETION_REQUESTED:
        # A code minted before the account was marked can still be confirmed during the
        # mark-to-purge window; the dying account must not gain a subscription row or a tier.
        log.warning(
            "Patreon pairing code confirmed by a user pending deletion",
            flow=LINK_FLOW,
            stage="persist",
            outcome=LinkOutcome.PENDING_DELETION.name.lower(),
            tg_user_id=user.tg_user_id,
        )
        return LinkOutcome.PENDING_DELETION

    claimed_elsewhere = (
        await session.exec(
            select(SupporterSubscription).where(SupporterSubscription.patreon_user_id == patreon_user_id)
        )
    ).first()
    if claimed_elsewhere is not None and claimed_elsewhere.user_id != user.db_id:
        log.warning(
            "Patreon account already linked to another Telegram user",
            flow=LINK_FLOW,
            stage="persist",
            outcome=LinkOutcome.ALREADY_LINKED_ELSEWHERE.name.lower(),
            patreon_user_id=patreon_user_id,
            tg_user_id=user.tg_user_id,
            linked_user_id=claimed_elsewhere.user_id,
        )
        return LinkOutcome.ALREADY_LINKED_ELSEWHERE

    linked = await upsert_subscription(session, user, patreon_user_id)
    if linked is None:
        # Lost the uniqueness race: another account committed this Patreon id first. Nothing on this
        # user has been touched yet, so there is nothing to undo.
        log.warning(
            "Patreon account was linked to another Telegram user concurrently",
            flow=LINK_FLOW,
            stage="persist",
            outcome=LinkOutcome.ALREADY_LINKED_ELSEWHERE.name.lower(),
            patreon_user_id=patreon_user_id,
            tg_user_id=user.tg_user_id,
        )
        return LinkOutcome.ALREADY_LINKED_ELSEWHERE

    subscription = linked.subscription
    if supporter.is_supporter(granted_level):
        user.supporter_level = granted_level
        subscription.support_expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=SUPPORT_GRACE_DAYS)
        message = SupporterNotificationMessages.unlocked_for(granted_level)
        outcome = LinkOutcome.LINKED_SUPPORTER
    else:
        user.supporter_level = SupporterLevel.NONE
        subscription.support_expiration = None
        message = CollaborateMessages.LINK_CONFIRMED_NO_PATRON
        outcome = LinkOutcome.LINKED_NO_PATRON

    log.info(
        "Patreon account linked",
        flow=LINK_FLOW,
        stage="persist",
        outcome=outcome.name.lower(),
        tg_user_id=user.tg_user_id,
        patreon_user_id=patreon_user_id,
        supporter_level=granted_level.value,
        link_kind=str(linked.kind),
        previous_patreon_user_id=linked.previous_patreon_user_id,
    )
    # The confirmation DM carries a Main-menu button so the user is never stranded on a
    # button-less message.
    await api.send_message_to_user(user, link_confirmation_view(message.get(lang=user.lang), user.lang))
    if outcome is LinkOutcome.LINKED_SUPPORTER:
        await readmit_to_hosts_group(api, user, trigger=HostsGroupTrigger.LINK)
    return outcome
