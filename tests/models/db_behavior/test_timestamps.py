import datetime as dt
from collections.abc import Awaitable, Callable

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import Meetup, Settings, User

pytestmark = pytest.mark.db_test


# --- INSERT path helpers ---


async def _user_timestamps(
    db_session: AsyncSession, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    await db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


async def _settings_timestamps(
    db_session: AsyncSession, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = (await db_session.exec(select(Settings).where(Settings.user_id == seed_user.id))).one()
    await db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


async def _meetup_timestamps(
    db_session: AsyncSession, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    # Earlier tests' savepoint rollbacks may have expired the shared seed instance, and even
    # reading seed_meetup.id would then lazy-load — refresh it explicitly first.
    await db_session.refresh(seed_meetup)
    loaded = (await db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id))).one()
    await db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


@pytest.mark.parametrize(
    "get_timestamps",
    [_user_timestamps, _settings_timestamps, _meetup_timestamps],
    ids=["users", "settings", "meetups"],
)
async def test_timestamps_are_set_on_insert(
    db_session: AsyncSession,
    seed_user: User,
    seed_meetup: Meetup,
    get_timestamps: Callable[[AsyncSession, User, Meetup], Awaitable[tuple[dt.datetime | None, dt.datetime | None]]],
) -> None:
    created_time, updated_time = await get_timestamps(db_session, seed_user, seed_meetup)
    assert created_time is not None
    assert isinstance(created_time, dt.datetime)
    assert updated_time is not None
    assert isinstance(updated_time, dt.datetime)


# --- UPDATE path helpers ---


async def _update_user(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    loaded.last_name = "trigger-check"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


async def _update_settings(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = (await db_session.exec(select(Settings).where(Settings.user_id == seed_user.id))).one()
    loaded.timezone = "Europe/Madrid"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


async def _update_meetup(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    # See _meetup_timestamps: the shared seed may be expired by earlier savepoint rollbacks.
    await db_session.refresh(seed_meetup)
    loaded = (await db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id))).one()
    loaded.description = "trigger-check"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


@pytest.mark.parametrize(
    "do_update",
    [_update_user, _update_settings, _update_meetup],
    ids=["users", "settings", "meetups"],
)
async def test_updated_time_is_set_after_update(
    db_session: AsyncSession,
    seed_user: User,
    seed_meetup: Meetup,
    do_update: Callable[[AsyncSession, User, Meetup], Awaitable[dt.datetime | None]],
) -> None:
    updated_time = await do_update(db_session, seed_user, seed_meetup)
    assert updated_time is not None
    assert isinstance(updated_time, dt.datetime)
