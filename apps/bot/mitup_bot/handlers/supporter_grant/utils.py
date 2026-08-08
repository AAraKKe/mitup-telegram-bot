from dataclasses import dataclass

import structlog
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import patreon_link, supporter
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.callback_data import ValidGrantCallbackData
from mitup_bot.emojis import Emojis
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handler_id import HandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.patreon_link import HostsGroupTrigger
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import GrantOperatorMessages, SupporterNotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.collaborate import grant_notification_view

log = structlog.get_logger(__name__)


async def find_target(session: AsyncSession, identifier: str) -> User | None:
    """Resolve the operator's input to a registered member: a numeric Telegram id, or a username
    with or without the leading @ (matched case-insensitively, as Telegram usernames are)."""
    if identifier.isdigit():
        statement = select(User).where(col(User.tg_user_id) == int(identifier), User.status == UserStatus.MEMBER)
    else:
        username = identifier.removeprefix("@")
        if not username:
            return None
        statement = select(User).where(
            func.lower(col(User.username)) == username.lower(), User.status == UserStatus.MEMBER
        )
    return (await session.exec(statement)).first()


async def load_target(session: AsyncSession, user_id: int) -> User | None:
    """Re-load the callback's target row, refusing anything that is no longer a registered member."""
    target = await session.get(User, user_id)
    if target is None or target.status is not UserStatus.MEMBER:
        return None
    return target


async def patreon_linked(session: AsyncSession, target: User) -> bool:
    subscription = (
        await session.exec(select(SupporterSubscription).where(SupporterSubscription.user_id == target.db_id))
    ).first()
    return subscription is not None


def picked_level(valid: ValidGrantCallbackData, handler_id: HandlerId) -> SupporterLevel:
    """The tier a validated grant callback names. A rank outside `LEVEL_ORDER` is malformed data."""
    if valid.level >= len(supporter.LEVEL_ORDER):
        raise MalformedCallbackData(handler_id, cb.SET_GRANT_LEVEL.with_level(valid.id, valid.level))
    return supporter.LEVEL_ORDER[valid.level]


def target_prompt_view(lang: str) -> MitupView:
    """The identifier prompt shown on the admin-menu message, with a Cancel button so the operator
    is never stranded on a keyboard-less message."""
    return MitupView(
        GrantOperatorMessages.TARGET_PROMPT.get(lang=lang),
        [[cancel_button(lang)]],
    )


def target_summary_view(lang: str, target: User, *, linked: bool) -> MitupView:
    """The resolved target's current state plus the tier picker.

    Every picker button carries the target's row id and the tier rank, so the flow needs no
    per-operator draft state and a stale button re-resolves everything server-side.
    """
    return MitupView(
        GrantOperatorMessages.TARGET_SUMMARY.get(
            lang=lang,
            name=target.display_name,
            tg_user_id=target.tg_user_id,
            current_level=GrantOperatorMessages.level_label(target.supporter_level).get(lang=lang),
            granted_level=GrantOperatorMessages.level_label(target.granted_supporter_level).get(lang=lang),
            patreon_linked=Emojis.boolean(linked),
        ),
        [
            [level_button(lang, target, SupporterLevel.HOST_1), level_button(lang, target, SupporterLevel.HOST_2)],
            [level_button(lang, target, SupporterLevel.HOST_3), level_button(lang, target, SupporterLevel.NONE)],
            [cancel_button(lang)],
        ],
    )


def level_button(lang: str, target: User, level: SupporterLevel) -> ButtonConfig:
    label = (
        GrantOperatorMessages.BUTTON_REMOVE_GRANT
        if level is SupporterLevel.NONE
        else GrantOperatorMessages.level_label(level)
    )
    return ButtonConfig(
        text=label.get_text(lang=lang),
        callback_data=cb.SET_GRANT_LEVEL.with_level(target.db_id, supporter.rank(level)),
    )


def cancel_button(lang: str) -> ButtonConfig:
    return ButtonConfig(text=GrantOperatorMessages.BUTTON_CANCEL.get_text(lang=lang), callback_data=cb.CANCEL_GRANT)


@dataclass(frozen=True, slots=True)
class GrantOutcome:
    """What applying a grant changed, for the confirmation screen and the audit log line."""

    previous_level: SupporterLevel
    new_level: SupporterLevel
    previous_granted_level: SupporterLevel
    linked: bool


async def apply_grant(
    session: AsyncSession, api: TelegramApiWrapper, target: User, level: SupporterLevel
) -> GrantOutcome:
    """Set the target's granted floor and bring their effective level, hosts-group access, and
    notification in line with it.

    A linked user's level only ever moves up here: lowering the floor leaves the stored level for
    the daily reconciliation to settle against their real entitlement, so a paying patron is never
    dropped below what they pay for while the sweep catches up. An unlinked user has no earned tier,
    so their level is exactly the floor.
    """
    previous_level = target.supporter_level
    previous_granted = target.granted_supporter_level
    linked = await patreon_linked(session, target)

    target.granted_supporter_level = level
    target.supporter_level = supporter.highest(target.supporter_level, level) if linked else level

    await notify_target(api, target, previous_level)
    await reconcile_hosts_group(api, target, previous_level)
    return GrantOutcome(
        previous_level=previous_level,
        new_level=target.supporter_level,
        previous_granted_level=previous_granted,
        linked=linked,
    )


async def notify_target(api: TelegramApiWrapper, target: User, previous_level: SupporterLevel):
    """DM the target about their effective-level change; silent when nothing visible changed."""
    new_level = target.supporter_level
    if new_level is previous_level:
        return
    if not supporter.meets(previous_level, new_level):
        message = SupporterNotificationMessages.granted_for(new_level)
    elif supporter.is_supporter(new_level):
        message = SupporterNotificationMessages.downgraded_to(new_level)
    else:
        message = SupporterNotificationMessages.GRANT_REMOVED
    await api.send_message_to_user(target, grant_notification_view(message.get(lang=target.lang), target.lang))


async def reconcile_hosts_group(api: TelegramApiWrapper, target: User, previous_level: SupporterLevel):
    """Lift a hosts-group ban for a user the grant just made a supporter, and withdraw one it just
    demoted below supporter. Tier-to-tier moves leave group state untouched."""
    was_supporter = supporter.is_supporter(previous_level)
    is_now_supporter = supporter.is_supporter(target.supporter_level)
    if not was_supporter and is_now_supporter:
        await patreon_link.readmit_to_hosts_group(api, target, trigger=HostsGroupTrigger.GRANT)
    elif was_supporter and not is_now_supporter:
        await patreon_link.withdraw_from_hosts_group(api, target)
