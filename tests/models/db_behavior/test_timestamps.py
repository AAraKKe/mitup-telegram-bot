import datetime as dt
from collections.abc import Awaitable, Callable

import pytest
from sqlmodel import select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import JoinedUsers, Meetup, Settings, User

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
):
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
):
    updated_time = await do_update(db_session, seed_user, seed_meetup)
    assert updated_time is not None
    assert isinstance(updated_time, dt.datetime)


# --- JoinedUsers created_time reflects insert time, not process start (#191) ---


@pytest.mark.usefixtures("db_session")
async def test_joined_users_created_time_is_per_insert():
    """Separately-committed joins must persist distinct, insert-ordered created_time values.

    Pins #191: a Python-side class-level default would stamp every row of a process with
    the same import-time constant, making waiting-list promotion order (sorted by
    created_time) a tie across the whole deploy. The DB trigger must own the column.
    """
    # Committed cross-session data uses the 997 range per the db-integration reference;
    # this file claims the 997_2xx sub-range.
    # Seed outside the try: if this transaction fails nothing was committed, so there is
    # nothing for the finally block to clean up (and meetup_id would be unbound).
    async with db.begin() as session:
        owner = User(first_name="Timestamps Owner", tg_user_id=997_200, settings=Settings())
        first = User(first_name="Timestamps First", tg_user_id=997_201, settings=Settings())
        second = User(first_name="Timestamps Second", tg_user_id=997_202, settings=Settings())
        meetup = Meetup(
            title="Timestamps Meeting",
            waiting_list=True,
            public=False,
            allow_invitation=False,
            incognito=False,
            owner=owner,
        )
        session.add_all([owner, first, second, meetup])
        await session.flush()
        meetup_id = meetup.db_id
        first_id = first.db_id
        second_id = second.db_id

    try:
        async with db.begin() as session:
            session.add(JoinedUsers(user_id=first_id, meetup_id=meetup_id, is_waiting_list=True))

        async with db.begin() as session:
            session.add(JoinedUsers(user_id=second_id, meetup_id=meetup_id, is_waiting_list=True))

        async with db.begin() as session:
            rows = (
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text(
                        "SELECT user_id, created_time FROM joined_users WHERE meetup_id = :meetup_id ORDER BY id"
                    ).bindparams(meetup_id=meetup_id)
                )
            ).all()

        assert [row[0] for row in rows] == [first_id, second_id]
        first_created, second_created = (row[1] for row in rows)
        assert first_created is not None
        assert second_created is not None
        # Strictly increasing: each row is stamped at its own insert transaction, so the
        # persisted order matches real join order instead of collapsing into one tie.
        assert first_created < second_created
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM joined_users WHERE meetup_id = :m").bindparams(m=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM meetups WHERE id = :m").bindparams(m=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "DELETE FROM settings WHERE user_id IN"
                    " (SELECT id FROM users WHERE tg_user_id BETWEEN 997200 AND 997299)"
                )
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id BETWEEN 997200 AND 997299")
            )
