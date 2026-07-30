import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import patreon, supporter
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.patreon import oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
)
from mitup_bot.views.mitup_view import MitupView

log = structlog.get_logger(__name__)


async def subscription_for_user(session: AsyncSession, user: User) -> SupporterSubscription | None:
    """Return the user's supporter subscription row, or None when they have never linked."""
    statement = select(SupporterSubscription).where(SupporterSubscription.user_id == user.id)
    return (await session.exec(statement)).first()


async def hosts_group_button_state(context: TMitupContext, tg_user_id: int) -> tuple[str | None, bool]:
    """Resolve the Hosts-Only Group invite link and whether the user is currently in the group.

    Returns ``(None, False)`` when the feature is unconfigured (either config value missing), so the
    view omits the group row. When configured, the boolean comes from a live ``is_chat_member`` check;
    that call already degrades to False on any Telegram failure, which renders the Join label.
    """
    bot_config = context.bot_config
    chat_id = bot_config.hosts_group_chat_id
    invite_url = bot_config.hosts_group_invite_url
    if chat_id is None or invite_url is None:
        # Half-configured is the interesting case: the row silently disappears from a Host's screen
        # while every other supporter perk keeps working, so name which half is missing.
        reason = "chat_id_not_configured" if chat_id is None else "invite_url_not_configured"
        log.info("Hosts-only group row omitted", stage="render", reason=reason)
        return None, False
    in_group = await context.api.is_chat_member(chat_id, tg_user_id)
    # The probe degrades to False on any Telegram failure, so a Join label on a Host who is already
    # in the group is only explicable next to this line and the api wrapper's refusal warning.
    log.info("Hosts-only group membership probed", stage="render", hosts_group_chat_id=chat_id, in_group=in_group)
    return invite_url, in_group


async def build_collaborate_view(session: AsyncSession, user: User, context: TMitupContext) -> MitupView:
    """Resolve which of the three Collaborate states the user is in and build its view.

    The resolved state is logged rather than inferred later: "I was shown the wrong screen" is
    otherwise answerable only by replaying the Patreon trail against a since-changed row. The
    `not_linked` line doubles as the record that an authorization URL was issued — never the URL
    itself, which carries signed state.
    """
    config = patreon.current_config()
    subscription = await subscription_for_user(session, user)
    if subscription is None:
        log_screen_resolved(user, state="not_linked", has_subscription=False)
        return collaborate_not_linked_view(user.lang, oauth.authorization_url(config))
    if supporter.is_supporter(user.supporter_level):
        active_meetings = supporter.active_meetings_cap(SupporterLevel.HOST_2)
        scheduling_days = supporter.scheduling_horizon_days(SupporterLevel.HOST_2)
        log_screen_resolved(user, state="linked_patron", has_subscription=True)
        hosts_group_url, in_group = await hosts_group_button_state(context, user.tg_user_id)
        return collaborate_linked_patron_view(
            user.lang, user.supporter_level, active_meetings, scheduling_days, hosts_group_url, in_group
        )
    log_screen_resolved(user, state="linked_not_patron", has_subscription=True)
    return collaborate_linked_not_patron_view(user.lang, oauth.campaign_pledge_url(config))


def log_screen_resolved(user: User, *, state: str, has_subscription: bool):
    log.info(
        "Collaborate screen resolved",
        user_id=user.db_id,
        stage="render",
        state=state,
        supporter_level=user.supporter_level.value,
        has_subscription=has_subscription,
    )
