from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Message, Update

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


def tapped_message_id(update: Update) -> int | None:
    """The message id of the tapped Collaborate button, or None when it is unavailable.

    Threaded into the OAuth ``state`` so the web callback can refresh this message after linking.
    PTB delivers an ``InaccessibleMessage`` (no usable id) when the original message is too old, so
    anything that is not a real ``Message`` degrades to None and the refresh is simply skipped.
    """
    query = update.callback_query
    if query is None:
        return None
    return query.message.message_id if isinstance(query.message, Message) else None


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
        return None, False
    in_group = await context.api.is_chat_member(chat_id, tg_user_id)
    return invite_url, in_group


async def build_collaborate_view(
    session: AsyncSession, user: User, context: TMitupContext, message_id: int | None = None
) -> MitupView:
    """Resolve which of the three Collaborate states the user is in and build its view.

    ``message_id`` is the Collaborate message being rendered; it is threaded into the OAuth ``state``
    on the not-linked branch only, so the web callback can refresh that message after linking.
    """
    config = patreon.current_config()
    subscription = await subscription_for_user(session, user)
    if subscription is None:
        authorization_url = oauth.authorization_url(config, user.tg_user_id, message_id)
        return collaborate_not_linked_view(user.lang, authorization_url)
    if supporter.is_supporter(user.supporter_level):
        active_meetings = supporter.active_meetings_cap(SupporterLevel.HOST_2)
        scheduling_days = supporter.scheduling_horizon_days(SupporterLevel.HOST_2)
        hosts_group_url, in_group = await hosts_group_button_state(context, user.tg_user_id)
        return collaborate_linked_patron_view(
            user.lang, user.supporter_level, active_meetings, scheduling_days, hosts_group_url, in_group
        )
    return collaborate_linked_not_patron_view(user.lang, oauth.campaign_pledge_url(config))
