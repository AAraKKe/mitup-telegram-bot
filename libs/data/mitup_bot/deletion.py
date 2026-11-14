"""Row removals shared by the bot and the recurrent-events runner."""

from collections.abc import Sequence
from typing import Protocol, cast

import structlog
from sqlmodel import col, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import Meetup, User
from mitup_bot.monitoring.outbound import qualified_type

log = structlog.get_logger(__name__)


class MeetingCardApi(Protocol):
    """The card fan-out a deletion drives, expressed structurally so this module never depends on the api."""

    async def update_meeting_messages(self, *, meeting: Meetup, was_deleted: bool = False): ...


async def mark_meeting_cards_deleted(api: MeetingCardApi, meetups: Sequence[Meetup]):
    """Rewrite every tracked card of *meetups* into the deleted banner.

    Call it under the write lifecycle and before the DELETE: the cards render from the rows the
    DELETE removes, and reach Telegram only once it has committed. A meeting whose cards fail to
    render is logged and skipped, so the deletion goes ahead and those cards stay as they were.
    """
    for meetup in meetups:
        try:
            await api.update_meeting_messages(meeting=meetup, was_deleted=True)
        except Exception as error:
            log.exception(
                "Meeting cards not marked deleted",
                meeting_id=meetup.id,
                error_type=qualified_type(error),
                exc_info=error,
                reason="render_failed",
            )


async def purge_meetups(session: AsyncSession, api: MeetingCardApi, meetups: Sequence[Meetup]) -> list[int]:
    """Delete *meetups*, cascading to their links and messages, and with them the invited users
    that exist only inside one of them. Returns the ids of the invited users it destroyed.

    The invitee rows have no other trace, so they are named on a log line before the DELETE.
    *api* must be in write-capture mode, or the card edits reach Telegram before the DELETE commits.
    """
    meeting_ids = [cast(int, meetup.id) for meetup in meetups]
    # Invited users exist only in the context of the meeting they were invited to.
    invitee_ids = [
        cast(int, link.user.id) for meetup in meetups for link in meetup.joined_links if link.user.tg_user_id == -1
    ]

    await mark_meeting_cards_deleted(api, meetups)

    log.info("Invitee users purged", count=len(invitee_ids), user_ids=invitee_ids, reason="cascade_of_purged_meetups")

    await session.exec(delete(Meetup).where(col(Meetup.id).in_(meeting_ids)))
    await session.exec(delete(User).where(col(User.id).in_(invitee_ids)))
    return invitee_ids
