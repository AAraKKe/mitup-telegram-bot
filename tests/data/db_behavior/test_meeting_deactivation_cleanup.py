"""DB-integration tests for what the expiration sweep erases along with a meeting, and what
reactivation clears.

The deletes are bulk SQL statements and ORM cascades whose real effect a mock session cannot replay.
Throwaway data uses the 998_9xx range (single-session, never committed).
"""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.events import inactive_meetings
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, User
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
        datetime=dt.datetime.now(dt.UTC) - dt.timedelta(days=2),
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


async def message_count(session: AsyncSession, meetup_id: int) -> int:
    result = await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text("SELECT count(*) FROM messages WHERE meetup_id = :mid").bindparams(mid=meetup_id)
    )
    return result.scalar_one()


async def test_deactivation_keeps_participants_and_deletes_guests_and_cards(db_session: AsyncSession):
    """Participants and the waiting list keep their rows; the invited guest, which exists nowhere
    else, is deleted with its link, and the message cards stop being tracked."""
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
            Message(meetup=meeting, chat_id=998_901, message_id=1),
        ]
    )
    await db_session.flush()
    meetup_id = meeting.db_id
    invited_id = invited.db_id
    assert await membership_count(db_session, meetup_id) == 3

    assert await inactive_meetings.deactivate_meeting_locked(db_session, dated_nomination(meetup_id), MockApi()) is True
    await db_session.flush()

    assert meeting.active is False
    assert await membership_count(db_session, meetup_id) == 2
    assert await message_count(db_session, meetup_id) == 0
    assert await user_exists(db_session, invited_id) is False
    assert await user_exists(db_session, participant.db_id) is True
    assert await user_exists(db_session, waiting.db_id) is True


async def test_reactivated_meeting_starts_with_no_participants(db_session: AsyncSession):
    """Clearing the collection, as the reactivate handler does, deletes every kept row, the owner's
    own link included while it sits in their loaded `joined_links`."""
    owner = make_user(998_910, name="Reactivation Owner")
    participant = make_user(998_911, name="Reactivation Participant")
    waiting = make_user(998_912, name="Reactivation Waiting")
    db_session.add_all([owner, participant, waiting])
    await db_session.flush()

    meeting = make_expired_meetup(owner)
    db_session.add(meeting)
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=owner, meetup=meeting),
            JoinedUsers(user=participant, meetup=meeting),
            JoinedUsers(user=waiting, meetup=meeting, is_waiting_list=True),
        ]
    )
    await db_session.flush()

    assert (
        await inactive_meetings.deactivate_meeting_locked(db_session, dated_nomination(meeting.db_id), MockApi())
        is True
    )
    await db_session.flush()
    assert await membership_count(db_session, meeting.db_id) == 3
    await db_session.refresh(owner, ["joined_links", "meetups"])

    meeting.active = True
    meeting.joined_links.clear()
    await db_session.flush()
    await db_session.refresh(meeting, ["joined_links"])

    assert meeting.joined_links == []
    assert await membership_count(db_session, meeting.db_id) == 0
    assert await user_exists(db_session, participant.db_id) is True
    assert await user_exists(db_session, waiting.db_id) is True
