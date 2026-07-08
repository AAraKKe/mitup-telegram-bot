from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test

OWNER_TG_USER_ID = 998_640
JOINER_TG_USER_ID = 998_641


def async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


async def seed_user_with_collections(session: AsyncSession):
    """Seed a user (JOINER) that both owns a meeting and has joined another, so both
    `User.meetups` and `User.joined_links` are non-empty."""
    owner = User(first_name="LC Owner", tg_user_id=OWNER_TG_USER_ID, settings=Settings())
    joiner = User(first_name="LC Joiner", tg_user_id=JOINER_TG_USER_ID, settings=Settings())
    owner_meeting = Meetup(
        title="LC Owner Meeting", waiting_list=False, public=False, allow_invitation=False, incognito=False, owner=owner
    )
    joiner_meeting = Meetup(
        title="LC Joiner Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=joiner,
    )
    session.add_all([owner, joiner, owner_meeting, joiner_meeting])
    await session.flush()
    session.add(JoinedUsers(user=joiner, meetup=owner_meeting))
    await session.flush()


async def test_load_collections_true_eager_loads_both_collections(db_session: AsyncSession):
    """The default `load_collections=True` traverses `meetups`/`joined_links` without a lazy load."""
    async with AsyncSession(async_engine(db_session)) as session:
        await seed_user_with_collections(session)
        # Force a clean re-load: the seeded instance is in the identity map with its collections
        # already populated from the flush, which would mask whether by_tg_user_id loaded them.
        session.expunge_all()

        joiner = await User.by_tg_user_id(session, JOINER_TG_USER_ID, must_exist=True)

        assert len(joiner.meetups) == 1
        assert len(joiner.joined_links) == 1


async def test_load_collections_false_leaves_collections_lazy_raise(db_session: AsyncSession):
    """`load_collections=False` returns a lean instance: touching either collection trips the
    `lazy="raise"` guard instead of silently emitting a query. This is the structural safety net the
    settings-only handler flips rely on."""
    async with AsyncSession(async_engine(db_session)) as session:
        await seed_user_with_collections(session)
        session.expunge_all()

        joiner = await User.by_tg_user_id(session, JOINER_TG_USER_ID, must_exist=True, load_collections=False)

        with pytest.raises(InvalidRequestError):
            _ = joiner.meetups
        with pytest.raises(InvalidRequestError):
            _ = joiner.joined_links
