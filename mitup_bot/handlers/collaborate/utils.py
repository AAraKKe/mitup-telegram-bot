from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import patreon, supporter
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.patreon import oauth
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
    collaborate_unavailable_view,
)
from mitup_bot.views.mitup_view import MitupView


async def subscription_for_user(session: AsyncSession, user: User) -> SupporterSubscription | None:
    """Return the user's supporter subscription row, or None when they have never linked."""
    statement = select(SupporterSubscription).where(SupporterSubscription.user_id == user.id)
    return (await session.exec(statement)).first()


async def build_collaborate_view(session: AsyncSession, user: User) -> MitupView:
    """Resolve which of the four Collaborate states the user is in and build its view."""
    if not patreon.is_configured():
        return collaborate_unavailable_view(user.lang)

    config = patreon.current_config()
    subscription = await subscription_for_user(session, user)
    if subscription is None:
        return collaborate_not_linked_view(user.lang, oauth.authorization_url(config, user.tg_user_id))
    if supporter.is_supporter(user.supporter_level):
        return collaborate_linked_patron_view(user.lang)
    return collaborate_linked_not_patron_view(user.lang, oauth.campaign_pledge_url(config))
