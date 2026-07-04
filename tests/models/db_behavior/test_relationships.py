import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


async def test_user_has_settings_relationship(db_session: AsyncSession, seed_user: User):
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    assert loaded.settings is not None
    assert isinstance(loaded.settings, Settings)


async def test_user_has_meetups_relationship(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup):
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    # User.meetups is deliberately lazy (loaded explicitly by by_tg_user_id in production),
    # so load it explicitly here — the async engine cannot lazy-load on attribute access.
    await db_session.refresh(loaded, ["meetups"])
    assert seed_meetup.id in [m.id for m in loaded.meetups]


async def test_user_has_joined_links_relationship(
    db_session: AsyncSession, seed_second_user: User, seed_joined_link: JoinedUsers
):
    loaded = (await db_session.exec(select(User).where(User.id == seed_second_user.id))).one()
    # Same as User.meetups above: deliberately lazy, so load explicitly.
    await db_session.refresh(loaded, ["joined_links"])
    assert seed_joined_link.id in [jl.id for jl in loaded.joined_links]


async def test_meetup_has_owner_relationship(db_session: AsyncSession, seed_meetup: Meetup, seed_user: User):
    loaded = (await db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id))).one()
    assert loaded.owner is not None
    assert loaded.owner.id == seed_user.id


async def test_meetup_has_joined_links_relationship(
    db_session: AsyncSession, seed_meetup: Meetup, seed_joined_link: JoinedUsers
):
    loaded = (await db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id))).one()
    assert seed_joined_link.id in [jl.id for jl in loaded.joined_links]


async def test_joined_link_has_user_relationship(
    db_session: AsyncSession, seed_joined_link: JoinedUsers, seed_second_user: User
):
    loaded = (await db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id))).one()
    assert loaded.user is not None
    assert loaded.user.id == seed_second_user.id


async def test_joined_link_has_meetup_relationship(
    db_session: AsyncSession, seed_joined_link: JoinedUsers, seed_meetup: Meetup
):
    loaded = (await db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id))).one()
    assert loaded.meetup is not None
    assert loaded.meetup.id == seed_meetup.id


async def test_joined_link_invited_by_is_none(db_session: AsyncSession, seed_joined_link: JoinedUsers):
    loaded = (await db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id))).one()
    assert loaded.invited_by is None
    assert loaded.invited_by_id is None
