"""Concurrency tests for the expiration batch job's per-meeting row lock (issue #195).

The job racer runs the REAL per-meeting critical section (the undecorated inner of
``inactive_meetings._deactivate_meeting``) — lock the meetup row via
``Meetup.by_id(for_update=True)``, re-check eligibility, read ``joined_links``, mutate — in
its own ``db.begin()`` transaction against real Postgres, racing the live bot's critical
sections exactly as they interleave in production. Mock sessions cannot catch these races.

Committed cross-session data uses the 997 range per the db-integration reference; this file
claims the 997_3xx sub-range (997_0xx: test_meeting_row_locks, 997_1xx: test_commit_before_fanout).
"""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.cli import inactive_meetings
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from tests.helpers import MockApi
from tests.helpers.locking import race

pytestmark = pytest.mark.db_test


@contextlib.asynccontextmanager
async def _provisioned_expired_meeting(tg_base: int, *, users: int = 0) -> AsyncIterator[int]:
    """Provision a committed meeting that is due for deactivation and tear it down afterwards.

    Committed data is required here: the conftest seeds live in the session fixture's open
    transaction and are invisible to the concurrent ``db.begin()`` sessions these tests race.
    The meeting's ``datetime`` is set far enough in the past that any owner timeout has elapsed.
    """
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=2)
    async with db.begin() as session:
        owner = User(first_name="Sweep Owner", tg_user_id=tg_base, settings=Settings())
        extras = [
            User(first_name=f"Sweep User {offset}", tg_user_id=tg_base + offset, settings=Settings())
            for offset in range(1, users + 1)
        ]
        meetup = Meetup(
            title="Expired Meeting",
            waiting_list=False,
            public=False,
            allow_invitation=True,
            incognito=False,
            max_members=None,
            owner=owner,
            datetime=started,
        )
        session.add(owner)
        session.add_all(extras)
        session.add(meetup)
        await session.flush()
        meetup_id = meetup.db_id
    try:
        yield meetup_id
    finally:
        async with db.begin() as session:
            # Invited (tg_user_id == -1) rows the race may have leaked are only reachable
            # through their membership link, so they go first, before the meetup cascade
            # removes that link.
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "DELETE FROM users WHERE tg_user_id = -1"
                    " AND id IN (SELECT user_id FROM joined_users WHERE meetup_id = :mid)"
                ).bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id BETWEEN :lo AND :hi").bindparams(
                    lo=tg_base, hi=tg_base + users
                )
            )


async def _deactivate(session: AsyncSession, meetup_id: int) -> bool:
    """The batch job's per-meeting critical section exactly as production runs it. MockApi keeps
    the enqueued fan-out away from Telegram — the DB effects are what these tests race."""
    return await inactive_meetings._deactivate_meeting_locked(session, meetup_id, MockApi())


async def _invite_outside_user(session: AsyncSession, meetup_id: int, inviter_tg: int) -> int | None:
    """The invite-confirm critical section as callback_query_confirm_user_invitation runs it:
    lock the meetup row, bail out when the meeting is gone or inactive, insert the invited-user
    row through the racy_flush savepoint. Returns the invited user's id, None when rejected."""
    inviter = (await session.exec(select(User).where(User.tg_user_id == inviter_tg))).one()
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    if meeting is None or not meeting.active:
        return None
    invited_user = User(first_name="Outside Guest", tg_user_id=-1, status=UserStatus.JOINED_ONLY)
    joined_link = await db.racy_flush(
        session,
        lambda: meeting.add_participant(invited_user, invited_by=inviter),
        constraint=JOINED_USERS_UNIQUE_CONSTRAINT,
    )
    assert joined_link is not None
    return invited_user.db_id


async def _reschedule(session: AsyncSession, meetup_id: int, new_datetime: dt.datetime):
    """An owner moving the meeting to a new date: locked re-load, mutate datetime, flush."""
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    assert meeting is not None
    meeting.datetime = new_datetime
    await session.flush()


async def _committed_meeting_state(meetup_id: int) -> tuple[bool, dt.datetime | None, int]:
    """(active, expiration_time, membership row count) as visible to a fresh transaction."""
    async with db.begin() as session:
        meeting = await Meetup.by_id(session, meetup_id)
        assert meeting is not None
        return meeting.active, meeting.expiration_time, len(meeting.joined_links)


async def test_deactivation_racing_invite_rejects_the_invite(db_session: AsyncSession):
    """The job holds the lock first: the concurrent invite blocks on its locked load and, once
    the deactivation commits, finds the meeting inactive and bails. Without the job's lock the
    invite lands inside the window and its invited-user row leaks past the cleanup forever."""
    tg_base = 997_300
    async with _provisioned_expired_meeting(tg_base, users=1) as meetup_id:
        deactivated, invited_id = await race(
            lambda session: _deactivate(session, meetup_id),
            lambda session: _invite_outside_user(session, meetup_id, tg_base + 1),
        )

        assert deactivated is True
        assert invited_id is None  # the invite saw the committed active=False and rejected

        active, expiration_time, memberships = await _committed_meeting_state(meetup_id)
        assert active is False
        assert expiration_time is not None
        assert memberships == 0  # nothing joined the meeting after its deactivation


async def test_invite_racing_deactivation_leaks_no_invited_user(db_session: AsyncSession):
    """The invite holds the lock first: the job blocks behind it, and its post-lock re-read
    (populate_existing) sees the freshly committed invited-user link — so the invited row is
    cleaned up with the meeting. Without the lock the job decides on stale joined_links and
    the invited-user row survives deactivation with no remaining path to ever delete it."""
    tg_base = 997_310
    async with _provisioned_expired_meeting(tg_base, users=1) as meetup_id:
        invited_id, deactivated = await race(
            lambda session: _invite_outside_user(session, meetup_id, tg_base + 1),
            lambda session: _deactivate(session, meetup_id),
        )

        assert invited_id is not None  # landed before the job took the lock
        assert deactivated is True

        active, _, memberships = await _committed_meeting_state(meetup_id)
        assert active is False
        assert memberships == 0
        async with db.begin() as session:
            leaked_invited_rows = (
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("SELECT count(*) FROM users WHERE id = :uid").bindparams(uid=invited_id)
                )
            ).scalar_one()
        assert leaked_invited_rows == 0  # the locked re-read saw the invite; no orphan row


async def test_reschedule_racing_deactivation_keeps_meeting_active(db_session: AsyncSession):
    """An owner reschedule holds the lock first: the job, serialized behind it, re-checks the
    deactivation predicate under the lock and skips the no-longer-due meeting. Without the lock
    the job decides on the stale timing and deactivates a meeting that was just moved."""
    tg_base = 997_320
    future = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=2)
    async with _provisioned_expired_meeting(tg_base) as meetup_id:
        _, deactivated = await race(
            lambda session: _reschedule(session, meetup_id, future),
            lambda session: _deactivate(session, meetup_id),
        )

        assert deactivated is False  # the under-lock re-check saw the new datetime

        active, expiration_time, _ = await _committed_meeting_state(meetup_id)
        assert active is True
        assert expiration_time is None
