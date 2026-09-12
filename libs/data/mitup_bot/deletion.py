"""Row removals shared by the bot and the recurrent-events runner."""

from collections.abc import Sequence
from typing import cast

import structlog
from sqlmodel import col, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import Meetup, User

log = structlog.get_logger(__name__)


async def purge_meetups(session: AsyncSession, meetups: Sequence[Meetup]) -> list[int]:
    """Delete *meetups*, cascading to their links and messages, and with them the invited users
    that exist only inside one of them. Returns the ids of the invited users it destroyed.

    The invitee rows have no other trace, so they are named on a log line before the DELETE.
    """
    meeting_ids = [cast(int, meetup.id) for meetup in meetups]
    # Invited users exist only in the context of the meeting they were invited to.
    invitee_ids = [
        cast(int, link.user.id) for meetup in meetups for link in meetup.joined_links if link.user.tg_user_id == -1
    ]

    log.info("Invitee users purged", count=len(invitee_ids), user_ids=invitee_ids, reason="cascade_of_purged_meetups")

    await session.exec(delete(Meetup).where(col(Meetup.id).in_(meeting_ids)))
    await session.exec(delete(User).where(col(User.id).in_(invitee_ids)))
    return invitee_ids
