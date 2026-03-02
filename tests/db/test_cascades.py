import pytest
from sqlmodel import Session, select

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


def test_deleting_user_cascades_to_settings(db_session: Session) -> None:
    user = User(first_name="Cascade Test Settings", tg_user_id=998_001, settings=Settings())
    db_session.add(user)
    db_session.flush()

    user_id = user.id

    db_session.delete(user)
    db_session.flush()

    assert db_session.exec(select(Settings).where(Settings.user_id == user_id)).all() == []


def test_deleting_user_cascades_to_meetups(db_session: Session) -> None:
    user = User(first_name="Cascade Test Meetups", tg_user_id=998_002)
    user.settings = Settings()
    meetup = Meetup(
        title="Cascade Meetup",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=user,
    )
    db_session.add(user)
    db_session.add(meetup)
    db_session.flush()
    user_id = user.id

    db_session.delete(user)
    db_session.flush()

    assert db_session.exec(select(Meetup).where(Meetup.owner_id == user_id)).all() == []


def test_deleting_meetup_cascades_to_joined_users(db_session: Session) -> None:
    owner = User(first_name="Cascade Meetup Owner", tg_user_id=998_003)
    owner.settings = Settings()
    joiner = User(first_name="Cascade Meetup Joiner", tg_user_id=998_004)
    joiner.settings = Settings()
    meetup = Meetup(
        title="Cascade Target Meetup",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
    )
    db_session.add(owner)
    db_session.add(joiner)
    db_session.add(meetup)
    db_session.flush()

    link = JoinedUsers(user=joiner, meetup=meetup)
    db_session.add(link)
    db_session.flush()
    meetup_id = meetup.id

    db_session.delete(meetup)
    db_session.flush()

    assert db_session.exec(select(JoinedUsers).where(JoinedUsers.meetup_id == meetup_id)).all() == []
