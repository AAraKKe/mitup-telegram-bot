from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Message, Update

from mitup_bot import patreon, supporter
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.patreon import oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
    collaborate_unavailable_view,
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


async def build_collaborate_view(session: AsyncSession, user: User, message_id: int | None = None) -> MitupView:
    """Resolve which of the four Collaborate states the user is in and build its view.

    ``message_id`` is the Collaborate message being rendered; it is threaded into the OAuth ``state``
    on the not-linked branch only, so the web callback can refresh that message after linking.
    """
    if not patreon.is_configured():
        return collaborate_unavailable_view(user.lang)

    active_meetings = supporter.active_meetings_cap(SupporterLevel.HOST_2)
    scheduling_days = supporter.scheduling_horizon_days(SupporterLevel.HOST_2)

    config = patreon.current_config()
    subscription = await subscription_for_user(session, user)
    if subscription is None:
        authorization_url = oauth.authorization_url(config, user.tg_user_id, message_id)
        return collaborate_not_linked_view(user.lang, authorization_url, active_meetings, scheduling_days)
    if supporter.is_supporter(user.supporter_level):
        return collaborate_linked_patron_view(user.lang, user.supporter_level, active_meetings, scheduling_days)
    return collaborate_linked_not_patron_view(
        user.lang, oauth.campaign_pledge_url(config), active_meetings, scheduling_days
    )
