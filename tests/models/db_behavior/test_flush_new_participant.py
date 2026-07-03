from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.handlers.meeting.utils import flush_new_participant
from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


def _async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


async def _make_meeting(session: AsyncSession, tg_user_id: int) -> tuple[User, Meetup]:
    user = User(first_name="FNP Probe", tg_user_id=tg_user_id, settings=Settings())
    meetup = Meetup(
        title="FNP Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=user,
    )
    session.add(user)
    session.add(meetup)
    await session.flush()
    # Freshly flushed instances have never loaded their collections and the async engine
    # cannot lazy-load them on attribute access (mirrors the create-meeting handler).
    await session.refresh(meetup, ["joined_links"])
    await session.refresh(user, ["joined_links"])
    return user, meetup


async def test_flush_new_participant_recovers_session_on_clash(db_session: AsyncSession):
    """A duplicate join is a rolled-back no-op that leaves the transaction fully usable and the meeting's
    in-memory participant collection consistent with committed reality.

    Runs on a dedicated session on the same engine (nothing committed; rolled back on close) so
    ``flush_new_participant`` is the top-level savepoint, exactly as in production. ``db_session`` is
    depended on only to guarantee the schema is migrated.
    """
    async with AsyncSession(_async_engine(db_session)) as session:
        user, meeting = await _make_meeting(session, tg_user_id=998_500)
        # The membership that the concurrent writer already persisted (flushed within this transaction
        # is enough for the constraint to reject a duplicate).
        session.add(JoinedUsers(user=user, meetup=meeting))
        await session.flush()
        assert len(meeting.joined_links) == 1

        # A Message-like row already pending from earlier in the handler must survive the clash.
        sibling = User(first_name="FNP Sibling", tg_user_id=998_501, settings=Settings())
        session.add(sibling)

        # The join builds the duplicate link the way the handler does; via the meetup relationship it
        # lands in the in-memory collection as a phantom before the flush. add_participant's internal
        # membership check would autoflush the not-yet-persisted link, so guard it with no_autoflush.
        with session.no_autoflush:
            duplicate = meeting.add_participant(user)
        assert duplicate is not None
        assert len(meeting.joined_links) == 2

        # New signature: caller does NOT pre-add; the helper owns the savepoint.
        assert await flush_new_participant(session, meeting, duplicate) is False

        # The savepoint recovered the transaction rather than deactivating it...
        assert session.is_active
        # ...and the phantom is gone from the in-memory collection (expire reloaded committed state).
        assert len(meeting.joined_links) == 1

        # The surrounding transaction still commits its pending work.
        await session.flush()
        assert sibling.id is not None

        # The DB holds exactly one membership for the pair — no duplicate slipped through.
        rows = (
            await session.exec(
                select(JoinedUsers).where(JoinedUsers.user_id == user.id, JoinedUsers.meetup_id == meeting.id)
            )
        ).all()
        assert len(rows) == 1


async def test_flush_new_participant_persists_first_join(db_session: AsyncSession):
    """The success path inserts the row: the helper returns True and the membership is present in both
    the DB and the in-memory collection."""
    async with AsyncSession(_async_engine(db_session)) as session:
        user, meeting = await _make_meeting(session, tg_user_id=998_502)

        with session.no_autoflush:
            joined_link = meeting.add_participant(user)
        assert joined_link is not None

        assert await flush_new_participant(session, meeting, joined_link) is True

        assert len(meeting.joined_links) == 1
        rows = (
            await session.exec(
                select(JoinedUsers).where(JoinedUsers.user_id == user.id, JoinedUsers.meetup_id == meeting.id)
            )
        ).all()
        assert len(rows) == 1
