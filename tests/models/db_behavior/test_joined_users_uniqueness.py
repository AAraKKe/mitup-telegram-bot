import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


async def test_duplicate_membership_is_rejected(
    db_session: AsyncSession, seed_joined_link: JoinedUsers, seed_second_user: User, seed_meetup: Meetup
):
    """A second row with the same (user_id, meetup_id) as an existing membership violates
    the uq_joined_users_user_id_meetup_id constraint."""
    savepoint = await db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            db_session.add(JoinedUsers(user_id=seed_second_user.id, meetup_id=seed_meetup.id))
            await db_session.flush()
    finally:
        await savepoint.rollback()


async def test_distinct_user_same_meetup_is_allowed(
    db_session: AsyncSession, seed_joined_link: JoinedUsers, seed_user: User, seed_meetup: Meetup
):
    """A different user joining the same meetup does not collide — the constraint is on the pair."""
    savepoint = await db_session.begin_nested()
    try:
        link = JoinedUsers(user_id=seed_user.id, meetup_id=seed_meetup.id)
        db_session.add(link)
        await db_session.flush()
        assert link.id is not None
    finally:
        await savepoint.rollback()


async def test_same_user_distinct_meetup_is_allowed(
    db_session: AsyncSession, seed_joined_link: JoinedUsers, seed_second_user: User, seed_user: User
):
    """The same user joining a different meetup does not collide."""
    savepoint = await db_session.begin_nested()
    try:
        other_meetup = Meetup(
            title="Uniqueness Other Meetup",
            waiting_list=False,
            public=False,
            allow_invitation=False,
            incognito=False,
            owner=seed_user,
        )
        db_session.add(other_meetup)
        await db_session.flush()

        link = JoinedUsers(user_id=seed_second_user.id, meetup_id=other_meetup.id)
        db_session.add(link)
        await db_session.flush()
        assert link.id is not None
    finally:
        await savepoint.rollback()


@pytest.mark.parametrize("null_column", ["user_id", "meetup_id"], ids=["null_user_id", "null_meetup_id"])
async def test_null_key_rows_coexist(db_session: AsyncSession, seed_meetup: Meetup, null_column: str):
    """Postgres treats NULLs as distinct, so rows with a NULL user_id or meetup_id never collide,
    even when the other key is identical."""
    # Throwaway user in the 998 range to avoid colliding with session-scoped seed data.
    throwaway_user = User(first_name="Null Key Probe", tg_user_id=998_010, settings=Settings())
    db_session.add(throwaway_user)
    await db_session.flush()

    savepoint = await db_session.begin_nested()
    try:
        # Filter on the exact probe pair so the count is insensitive to any pre-existing NULL-key rows.
        if null_column == "user_id":
            probe_filter = (col(JoinedUsers.user_id).is_(None), JoinedUsers.meetup_id == seed_meetup.id)
            new_rows = [JoinedUsers(user_id=None, meetup_id=seed_meetup.id) for _ in range(2)]
        else:
            probe_filter = (col(JoinedUsers.meetup_id).is_(None), JoinedUsers.user_id == throwaway_user.id)
            new_rows = [JoinedUsers(user_id=throwaway_user.id, meetup_id=None) for _ in range(2)]

        before = len((await db_session.exec(select(JoinedUsers).where(*probe_filter))).all())
        for row in new_rows:
            db_session.add(row)
        await db_session.flush()

        after = len((await db_session.exec(select(JoinedUsers).where(*probe_filter))).all())
        assert after == before + 2
    finally:
        await savepoint.rollback()
        await db_session.delete(throwaway_user)
        await db_session.flush()
