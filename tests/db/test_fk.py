import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from mitup_bot.models import JoinedUsers, Meetup, User

pytestmark = pytest.mark.db_test


def test_invited_by_id_is_nullable(db_session: Session, seed_joined_link: JoinedUsers) -> None:
    loaded = db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id)).one()
    assert loaded.invited_by_id is None


def test_invited_by_fk_references_users(db_session: Session, seed_user: User, seed_meetup: Meetup) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.exec(  # type: ignore[call-overload]
                text(
                    "INSERT INTO joined_users (user_id, meetup_id, invited_by_id)"
                    " VALUES (:user_id, :meetup_id, 9999999)"
                ).bindparams(user_id=seed_user.id, meetup_id=seed_meetup.id)
            )
