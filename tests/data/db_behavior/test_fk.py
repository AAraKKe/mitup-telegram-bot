import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import JoinedUsers, Meetup, User

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
