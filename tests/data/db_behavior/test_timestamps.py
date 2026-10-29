import datetime as dt
from collections.abc import Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import StatementError
from sqlmodel import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.datetimes import local_to_utc
from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


# --- INSERT path helpers ---


async def user_timestamps(
    db_session: AsyncSession, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    await db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


async def settings_timestamps(
    db_session: AsyncSession, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = (await db_session.exec(select(Settings).where(Settings.user_id == seed_user.id))).one()
    await db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


async def meetup_timestamps(
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
    [user_timestamps, settings_timestamps, meetup_timestamps],
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
    assert created_time.utcoffset() == dt.timedelta(0)
    assert updated_time is not None
    assert updated_time.utcoffset() == dt.timedelta(0)


# --- UPDATE path helpers ---


async def update_user(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = (await db_session.exec(select(User).where(User.id == seed_user.id))).one()
    loaded.last_name = "trigger-check"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


async def update_settings(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = (await db_session.exec(select(Settings).where(Settings.user_id == seed_user.id))).one()
    loaded.timezone = "Europe/Madrid"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


async def update_meetup(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    # See meetup_timestamps: the shared seed may be expired by earlier savepoint rollbacks.
    await db_session.refresh(seed_meetup)
    loaded = (await db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id))).one()
    loaded.description = "trigger-check"
    await db_session.flush()
    await db_session.refresh(loaded)
    return loaded.updated_time


@pytest.mark.parametrize(
    "do_update",
    [update_user, update_settings, update_meetup],
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
    assert updated_time.utcoffset() == dt.timedelta(0)


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


# --- Time values round trip as aware UTC ---

MADRID = ZoneInfo("Europe/Madrid")


def make_meeting(owner: User, **fields: Any) -> Meetup:
    return Meetup(
        title="Round Trip Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        **fields,
    )


async def test_written_instants_come_back_in_utc(db_session: AsyncSession):
    savepoint = await db_session.begin_nested()
    try:
        owner = User(first_name="Round Trip Owner", tg_user_id=998_200, settings=Settings())
        meeting = make_meeting(
            owner,
            datetime=dt.datetime(2026, 7, 15, 20, 30, tzinfo=MADRID),
            expiration_time=dt.datetime(2026, 7, 16, 9, 0, 0, 250_000, tzinfo=MADRID),
            activated_time=dt.datetime(2026, 1, 10, 8, 15, tzinfo=MADRID),
        )
        join = JoinedUsers(user=owner, meetup=meeting, created_time=dt.datetime(2026, 3, 29, 3, 0, tzinfo=MADRID))
        db_session.add_all([owner, meeting, join])
        await db_session.flush()
        await db_session.refresh(meeting)
        await db_session.refresh(join)

        read_back = [meeting.datetime, meeting.expiration_time, meeting.activated_time, join.created_time]
        assert read_back == [
            dt.datetime(2026, 7, 15, 18, 30, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 16, 7, 0, 0, 250_000, tzinfo=dt.UTC),
            dt.datetime(2026, 1, 10, 7, 15, tzinfo=dt.UTC),
            dt.datetime(2026, 3, 29, 1, 0, tzinfo=dt.UTC),
        ]
        assert all(value is not None and value.tzinfo is dt.UTC for value in read_back)
    finally:
        await savepoint.rollback()


async def test_reads_do_not_depend_on_the_session_time_zone(db_session: AsyncSession):
    show_time_zone = text("SHOW TIME ZONE")
    time_zone_before = (await db_session.exec(show_time_zone)).scalar_one()  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
    savepoint = await db_session.begin_nested()
    try:
        owner = User(first_name="Session Zone Owner", tg_user_id=998_201, settings=Settings())
        meeting = make_meeting(owner, datetime=dt.datetime(2026, 11, 2, 23, 45, tzinfo=dt.UTC))
        db_session.add_all([owner, meeting])
        await db_session.flush()
        await db_session.exec(text("SET LOCAL TIME ZONE 'America/New_York'"))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        assert (await db_session.exec(show_time_zone)).scalar_one() == "America/New_York"  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657

        await db_session.refresh(meeting)

        start = meeting.datetime
        assert start == dt.datetime(2026, 11, 2, 23, 45, tzinfo=dt.UTC)
        assert start is not None and start.tzinfo is dt.UTC
    finally:
        await savepoint.rollback()
    assert (await db_session.exec(show_time_zone)).scalar_one() == time_zone_before  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657


async def test_writing_a_naive_value_is_rejected_before_it_reaches_the_database(
    db_session: AsyncSession, seed_user: User
):
    savepoint = await db_session.begin_nested()
    try:
        db_session.add(make_meeting(seed_user, datetime=dt.datetime(2026, 7, 15, 18, 30)))
        with pytest.raises(StatementError) as rejection:
            await db_session.flush()
        assert isinstance(rejection.value.orig, ValueError)
    finally:
        await savepoint.rollback()
    seed_user_rows = select(func.count()).select_from(User).where(User.tg_user_id == 999_001)
    assert (await db_session.exec(seed_user_rows)).one() == 1


@pytest.mark.parametrize(
    "local_start, stored_start",
    [
        (dt.datetime(2026, 7, 15, 20, 30), dt.datetime(2026, 7, 15, 18, 30, tzinfo=dt.UTC)),
        (dt.datetime(2026, 1, 15, 20, 30), dt.datetime(2026, 1, 15, 19, 30, tzinfo=dt.UTC)),
    ],
    ids=["summer", "winter"],
)
async def test_meeting_start_keeps_the_owner_wall_clock_across_daylight_saving(
    db_session: AsyncSession, local_start: dt.datetime, stored_start: dt.datetime
):
    savepoint = await db_session.begin_nested()
    try:
        owner = User(first_name="Madrid Owner", tg_user_id=998_202, settings=Settings(timezone="Europe/Madrid"))
        meeting = make_meeting(owner, datetime=local_to_utc(local_start.date(), local_start.time(), MADRID))
        db_session.add_all([owner, meeting])
        await db_session.flush()
        meeting_id = meeting.id
        db_session.expunge(meeting)

        reloaded = (await db_session.exec(select(Meetup).where(Meetup.id == meeting_id))).one()

        start = reloaded.datetime
        assert reloaded is not meeting
        assert start is not None and start == stored_start
        assert owner.datetime_in_tz(start).replace(tzinfo=None) == local_start
    finally:
        await savepoint.rollback()
