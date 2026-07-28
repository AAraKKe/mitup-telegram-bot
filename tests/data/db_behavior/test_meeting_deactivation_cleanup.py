"""DB-integration tests for what the expiration sweep erases along with a meeting.

`deactivate_meeting_locked` empties the meeting: the invited placeholder users (who exist only
inside it) and every membership row, participants and waiting list alike. The deletes are bulk SQL
statements whose real effect — the DB-level cascade from the invited users and what the collections
read back afterwards — a mock session cannot replay, so the cases live here. Throwaway data uses the
998_9xx range (single-session, never committed).
"""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.events import inactive_meetings
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus
from tests.helpers import MockApi

from .conftest import dated_nomination

pytestmark = pytest.mark.db_test


def make_user(tg_user_id: int, *, name: str) -> User:
    return User(first_name=name, tg_user_id=tg_user_id, settings=Settings())


def make_expired_meetup(owner: User) -> Meetup:
    """A meeting whose start is far enough in the past that any owner timeout has elapsed."""
    return Meetup(
        title="Expired Meeting",
        waiting_list=True,
        public=False,
        allow_invitation=True,
        incognito=False,
        owner=owner,
        datetime=dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=2),
    )


async def membership_count(session: AsyncSession, meetup_id: int) -> int:
    result = await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text("SELECT count(*) FROM joined_users WHERE meetup_id = :mid").bindparams(mid=meetup_id)
    )
    return result.scalar_one()


async def user_exists(session: AsyncSession, user_id: int) -> bool:
    result = await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text("SELECT count(*) FROM users WHERE id = :uid").bindparams(uid=user_id)
    )
    return result.scalar_one() == 1


async def test_deactivation_clears_every_membership(db_session: AsyncSession):
    """Participants, waiting-list entries and invited guests all leave the meeting.

    The real users survive — only their membership goes; the invited placeholder, which exists
    nowhere else, is deleted outright.
    """
    owner = make_user(998_900, name="Cleanup Owner")
    participant = make_user(998_901, name="Cleanup Participant")
    waiting = make_user(998_902, name="Cleanup Waiting")
    invited = User(first_name="Cleanup Guest", tg_user_id=-1, status=UserStatus.JOINED_ONLY)
    db_session.add_all([owner, participant, waiting, invited])
    await db_session.flush()

    meeting = make_expired_meetup(owner)
    db_session.add(meeting)
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=participant, meetup=meeting),
            JoinedUsers(user=waiting, meetup=meeting, is_waiting_list=True),
            JoinedUsers(user=invited, meetup=meeting, invited_by=owner),
        ]
    )
    await db_session.flush()
    meetup_id = meeting.db_id
    invited_id = invited.db_id
    assert await membership_count(db_session, meetup_id) == 3

    assert await inactive_meetings.deactivate_meeting_locked(db_session, dated_nomination(meetup_id), MockApi()) is True
    await db_session.flush()

    assert meeting.active is False
    assert await membership_count(db_session, meetup_id) == 0
    assert await user_exists(db_session, invited_id) is False
    assert await user_exists(db_session, participant.db_id) is True
    assert await user_exists(db_session, waiting.db_id) is True


async def test_reactivated_meeting_starts_with_no_participants(db_session: AsyncSession):
    """Reactivation brings back an empty meeting: there is no membership left to restore.

    Flipping `active` is the whole of what the reactivate handler writes, so a meeting coming back
    a year later cannot silently re-enrol whoever attended its previous run.
    """
    owner = make_user(998_910, name="Reactivation Owner")
    participant = make_user(998_911, name="Reactivation Participant")
    db_session.add_all([owner, participant])
    await db_session.flush()

    meeting = make_expired_meetup(owner)
    db_session.add(meeting)
    await db_session.flush()
    db_session.add(JoinedUsers(user=participant, meetup=meeting))
    await db_session.flush()

    assert (
        await inactive_meetings.deactivate_meeting_locked(db_session, dated_nomination(meeting.db_id), MockApi())
        is True
    )
    await db_session.flush()

    meeting.active = True
    meeting.expiration_time = None
    await db_session.flush()
    await db_session.refresh(meeting, ["joined_links"])

    assert meeting.joined_links == []
    assert meeting.n_participants == 0
    assert meeting.n_waiting == 0
