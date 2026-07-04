"""Real-Postgres behavior of ``db.racy_flush`` (inherited from the flush_new_participant tests).

The builder constructs the racy rows INSIDE the savepoint — the core lesson of !349: construction
is what makes rows session-pending through the relationship cascade, so building them inside
``begin_nested()`` lets a constraint clash roll them back without any expunge/re-add dance. These
tests must run on real Postgres: savepoint recovery, collection expiry on rollback and the
psycopg constraint diagnostics cannot be reproduced by mocks.
"""

import warnings
from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT

pytestmark = pytest.mark.db_test


def _async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


async def _make_meeting(session: AsyncSession, tg_user_id: int) -> tuple[User, Meetup]:
    user = User(first_name="RF Probe", tg_user_id=tg_user_id, settings=Settings())
    meetup = Meetup(
        title="RF Meeting",
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


async def test_racy_flush_recovers_session_on_clash(db_session: AsyncSession):
    """A duplicate join is a rolled-back no-op that leaves the transaction fully usable and the
    in-memory collections consistent with committed reality.

    Runs on a dedicated session on the same engine (nothing committed; rolled back on close) so
    ``racy_flush`` opens the top-level savepoint, exactly as in production. ``db_session`` is
    depended on only to guarantee the schema is migrated.
    """
    async with AsyncSession(_async_engine(db_session)) as session:
        user, meeting = await _make_meeting(session, tg_user_id=998_500)
        # The membership that the concurrent writer already persisted (flushed within this transaction
        # is enough for the constraint to reject a duplicate).
        session.add(JoinedUsers(user=user, meetup=meeting))
        await session.flush()
        await session.refresh(user, ["joined_links"])
        assert len(meeting.joined_links) == 1

        # A Message-like row already pending from earlier in the handler must survive the clash.
        sibling = User(first_name="RF Sibling", tg_user_id=998_501, settings=Settings())
        session.add(sibling)

        # The builder runs inside the savepoint and constructs the duplicate the way the handler
        # does; no SAWarning may leak (the old expunge/re-add dance used to provoke one).
        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            result = await db.racy_flush(
                session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
            )
        assert result is None

        # The savepoint recovered the transaction rather than deactivating it...
        assert session.is_active
        # ...and the phantom is gone from the in-memory collections: the rollback expired what
        # the builder touched and racy_flush reloaded it to committed state — including the
        # user's lazy="raise" collection, where the explicit refresh is the sanctioned route.
        assert len(meeting.joined_links) == 1
        assert len(user.joined_links) == 1

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


async def test_racy_flush_persists_first_join(db_session: AsyncSession):
    """The success path inserts the row: the helper returns the built link and the membership is
    present in both the DB and the in-memory collection."""
    async with AsyncSession(_async_engine(db_session)) as session:
        user, meeting = await _make_meeting(session, tg_user_id=998_502)

        joined_link = await db.racy_flush(
            session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
        )
        assert joined_link is not None
        assert joined_link.is_waiting_list is False

        assert len(meeting.joined_links) == 1
        rows = (
            await session.exec(
                select(JoinedUsers).where(JoinedUsers.user_id == user.id, JoinedUsers.meetup_id == meeting.id)
            )
        ).all()
        assert len(rows) == 1


async def test_racy_flush_inserts_backref_only_association(db_session: AsyncSession):
    """A row wired to its parents only through backref events is NOT cascaded into the session by
    SQLAlchemy 2.0 (it warns and skips the INSERT) — ``racy_flush``'s explicit ``session.add`` of
    the built row is what makes the flush land, warning-free."""
    async with AsyncSession(_async_engine(db_session)) as session:
        user, meeting = await _make_meeting(session, tg_user_id=998_503)

        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            link = await db.racy_flush(
                session,
                # Bare construction: the relationship assignments only associate the link with
                # the session via backref events, never via an add/cascade.
                lambda: JoinedUsers(user=user, meetup=meeting),
                constraint=JOINED_USERS_UNIQUE_CONSTRAINT,
            )

        assert link is not None
        assert link.id is not None  # flushed: the INSERT actually happened
        rows = (
            await session.exec(
                select(JoinedUsers).where(JoinedUsers.user_id == user.id, JoinedUsers.meetup_id == meeting.id)
            )
        ).all()
        assert len(rows) == 1
