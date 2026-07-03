import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT

pytestmark = pytest.mark.db_test

_MIGRATION_PATH = (
    Path(mitup_bot.__file__).parent
    / "migrations"
    / "versions"
    / "02557bf55f98_add_unique_constraint_on_joined_users_.py"
)


def _load_dedup_sql() -> str:
    """Load the exact DELETE the migration runs, so this test breaks if that statement changes.

    The revision module can't be imported by dotted path (its name starts with a digit), so load it
    from its file location and read the constant ``upgrade()`` itself executes.
    """
    spec = importlib.util.spec_from_file_location("_migration_02557bf55f98", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEDUPLICATE_JOINED_USERS_SQL


DEDUP_SQL = _load_dedup_sql()

_BASE_TIME = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


async def _make_group(db_session: AsyncSession, rows: list[tuple[bool, int]]) -> tuple[User, Meetup, list[JoinedUsers]]:
    """Create a throwaway user + meetup and a set of colliding memberships.

    ``rows`` is a list of (is_waiting_list, created_time_offset_hours). Each row is flushed in order so
    its serial id follows insertion order — matching the id tiebreak in the dedup ordering.
    """
    user = User(first_name="Dedup Probe", tg_user_id=998_300, settings=Settings())
    meetup = Meetup(
        title="Dedup Meetup",
        waiting_list=True,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=user,
    )
    db_session.add(user)
    db_session.add(meetup)
    await db_session.flush()

    links: list[JoinedUsers] = []
    for is_waiting_list, offset_hours in rows:
        link = JoinedUsers(
            user_id=user.id,
            meetup_id=meetup.id,
            is_waiting_list=is_waiting_list,
            created_time=_BASE_TIME + dt.timedelta(hours=offset_hours),
        )
        db_session.add(link)
        await db_session.flush()
        links.append(link)
    return user, meetup, links


@pytest.mark.parametrize(
    "rows, survivor_index",
    [
        # A non-waiting row always beats a waiting one; among the non-waiting rows the oldest wins.
        ([(True, 0), (False, 2), (False, 1)], 2),
        # All waiting: the oldest created_time survives.
        ([(True, 2), (True, 0), (True, 1)], 1),
        # Identical is_waiting_list and created_time: the id tiebreak keeps the lowest id (inserted first).
        ([(True, 0), (True, 0), (True, 0)], 0),
    ],
    ids=["mixed_keeps_oldest_non_waiting", "all_waiting_keeps_oldest", "id_tiebreak_keeps_lowest_id"],
)
async def test_dedup_keeps_expected_survivor(
    db_session: AsyncSession, rows: list[tuple[bool, int]], survivor_index: int
):
    """The migration dedup keeps exactly one row per (user_id, meetup_id), following the
    is_waiting_list ASC, created_time ASC, id ASC ordering."""
    savepoint = await db_session.begin_nested()
    try:
        # Drop the constraint so duplicates can be inserted, reproducing the pre-migration state.
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(f"ALTER TABLE joined_users DROP CONSTRAINT {JOINED_USERS_UNIQUE_CONSTRAINT}")
        )
        user, meetup, links = await _make_group(db_session, rows)
        expected_survivor_id = links[survivor_index].id

        await db_session.exec(text(DEDUP_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        await db_session.flush()

        remaining = (
            await db_session.exec(
                select(JoinedUsers).where(
                    JoinedUsers.user_id == user.id,
                    JoinedUsers.meetup_id == meetup.id,
                )
            )
        ).all()
        assert len(remaining) == 1
        assert remaining[0].id == expected_survivor_id
    finally:
        await savepoint.rollback()


async def test_dedup_leaves_null_key_rows_untouched(db_session: AsyncSession, seed_meetup: Meetup):
    """Rows with a NULL user_id or meetup_id are excluded from the dedup and never deleted, even when
    they look like duplicates of each other."""
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(f"ALTER TABLE joined_users DROP CONSTRAINT {JOINED_USERS_UNIQUE_CONSTRAINT}")
        )
        # Two rows sharing (NULL, seed_meetup.id) — indistinguishable to the constraint but NULL-keyed.
        null_links = [JoinedUsers(user_id=None, meetup_id=seed_meetup.id) for _ in range(2)]
        for link in null_links:
            db_session.add(link)
        await db_session.flush()

        await db_session.exec(text(DEDUP_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        await db_session.flush()

        remaining = (
            await db_session.exec(
                select(JoinedUsers).where(
                    col(JoinedUsers.user_id).is_(None),
                    JoinedUsers.meetup_id == seed_meetup.id,
                )
            )
        ).all()
        assert len(remaining) == 2
    finally:
        await savepoint.rollback()
