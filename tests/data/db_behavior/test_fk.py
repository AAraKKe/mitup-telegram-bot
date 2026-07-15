import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


async def test_invited_by_id_is_nullable(db_session: AsyncSession, seed_joined_link: JoinedUsers):
    loaded = (await db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id))).one()
    assert loaded.invited_by_id is None


async def test_invited_by_fk_references_users(db_session: AsyncSession, seed_user: User, seed_meetup: Meetup):
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "INSERT INTO joined_users (user_id, meetup_id, invited_by_id)"
                    " VALUES (:user_id, :meetup_id, 9999999)"
                ).bindparams(user_id=seed_user.id, meetup_id=seed_meetup.id)
            )


async def test_deleting_inviter_nulls_invited_by(db_session: AsyncSession):
    """Deleting a user who invited a still-joined participant leaves the join row standing with
    `invited_by_id` set to NULL, rather than raising an FK violation. The bulk DELETE the cleanup
    job issues would abort its whole batch on a bare FK; ON DELETE SET NULL detaches the reference.
    """
    inviter = User(first_name="Inviter FK", tg_user_id=998_040, settings=Settings())
    owner = User(first_name="Owner FK", tg_user_id=998_041, settings=Settings())
    joiner = User(first_name="Joiner FK", tg_user_id=998_042, settings=Settings())
    db_session.add_all([inviter, owner, joiner])
    await db_session.flush()

    meetup = Meetup(
        title="Invited-by FK Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
    )
    db_session.add(meetup)
    await db_session.flush()

    link = JoinedUsers(user=joiner, meetup=meetup, invited_by=inviter)
    db_session.add(link)
    await db_session.flush()
    link_id = link.id
    inviter_id = inviter.id

    await db_session.exec(delete(User).where(col(User.id) == inviter_id))  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
    await db_session.flush()

    reloaded = (
        await db_session.exec(
            select(JoinedUsers).where(JoinedUsers.id == link_id).execution_options(populate_existing=True)
        )
    ).one()
    assert reloaded.invited_by_id is None
