from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, MeetingCounts, Meetup, Settings, User

pytestmark = pytest.mark.db_test

COUNTED_TG_USER_ID = 998_780
HOST_TG_USER_ID = 998_781


def async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


def meeting(owner: User, title: str, *, active: bool = True) -> Meetup:
    return Meetup(
        title=title,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        active=active,
        owner=owner,
    )


async def seed_counted_user(session: AsyncSession) -> User:
    """A user owning three active and one finished meeting, joined to one of two meetings owned by
    somebody else."""
    counted = User(first_name="MC Counted", tg_user_id=COUNTED_TG_USER_ID, settings=Settings())
    host = User(first_name="MC Host", tg_user_id=HOST_TG_USER_ID, settings=Settings())
    host_active = meeting(host, "MC Host active")
    host_finished = meeting(host, "MC Host finished", active=False)
    session.add_all(
        [
            counted,
            host,
            meeting(counted, "MC Own active"),
            meeting(counted, "MC Own <b>tagged</b>"),
            meeting(counted, "   "),
            meeting(counted, "MC Own finished", active=False),
            host_active,
            host_finished,
        ]
    )
    await session.flush()
    session.add_all([JoinedUsers(user=counted, meetup=host_active), JoinedUsers(user=counted, meetup=host_finished)])
    await session.flush()
    return counted


async def test_meeting_counts_sizes_the_three_lists(db_session: AsyncSession):
    async with AsyncSession(async_engine(db_session)) as session:
        counted = await seed_counted_user(session)

        assert await counted.meeting_counts(session) == MeetingCounts(active=3, joined=1, past=1)


async def test_meeting_counts_are_zero_for_a_user_with_nothing(db_session: AsyncSession):
    async with AsyncSession(async_engine(db_session)) as session:
        lonely = User(first_name="MC Lonely", tg_user_id=998_782, settings=Settings())
        session.add(lonely)
        await session.flush()

        assert await lonely.meeting_counts(session) == MeetingCounts(active=0, joined=0, past=0)


@pytest.mark.parametrize(
    "title",
    ["", "   ", "\n\t", "<b></b>", "<b>   </b>", "<i>Real</i>", "Plain"],
    ids=["empty", "spaces", "newline_tab", "empty_tags", "tags_around_spaces", "tagged_text", "plain_text"],
)
async def test_the_active_count_covers_a_meeting_of_any_title_shape(db_session: AsyncSession, title: str):
    """The active list shows a meeting whose title has no visible text, naming it untitled, so the
    count covers it too."""
    async with AsyncSession(async_engine(db_session)) as session:
        owner = User(first_name="MC Titles", tg_user_id=998_783, settings=Settings())
        session.add_all([owner, meeting(owner, title)])
        await session.flush()

        assert (await owner.meeting_counts(session)).active == 1


async def test_meeting_counts_ignores_meetings_of_other_users(db_session: AsyncSession):
    async with AsyncSession(async_engine(db_session)) as session:
        counted = await seed_counted_user(session)
        stranger = User(first_name="MC Stranger", tg_user_id=998_784, settings=Settings())
        session.add_all([stranger, meeting(stranger, "MC Stranger active"), meeting(stranger, "MC Gone", active=False)])
        await session.flush()

        assert await counted.meeting_counts(session) == MeetingCounts(active=3, joined=1, past=1)


async def test_meeting_counts_leaves_the_collections_unloaded(db_session: AsyncSession):
    """The counts are the reason a menu render needs no `load_collections`, so they must not be
    what loads them."""
    async with AsyncSession(async_engine(db_session)) as session:
        await seed_counted_user(session)
        session.expunge_all()
        counted = await User.by_tg_user_id(session, COUNTED_TG_USER_ID, must_exist=True, load_collections=False)

        assert await counted.meeting_counts(session) == MeetingCounts(active=3, joined=1, past=1)
        for collection in ("meetups", "joined_links"):
            with pytest.raises(InvalidRequestError):
                getattr(counted, collection)
