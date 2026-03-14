import pytest
from sqlmodel import Session, select

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


def test_user_has_settings_relationship(db_session: Session, seed_user: User) -> None:
    loaded = db_session.exec(select(User).where(User.id == seed_user.id)).one()
    assert loaded.settings is not None
    assert isinstance(loaded.settings, Settings)


def test_user_has_meetups_relationship(db_session: Session, seed_user: User, seed_meetup: Meetup) -> None:
    loaded = db_session.exec(select(User).where(User.id == seed_user.id)).one()
    assert seed_meetup.id in [m.id for m in loaded.meetups]


def test_user_has_joined_links_relationship(
    db_session: Session, seed_second_user: User, seed_joined_link: JoinedUsers
) -> None:
    loaded = db_session.exec(select(User).where(User.id == seed_second_user.id)).one()
    assert seed_joined_link.id in [jl.id for jl in loaded.joined_links]


def test_meetup_has_owner_relationship(db_session: Session, seed_meetup: Meetup, seed_user: User) -> None:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    assert loaded.owner is not None
    assert loaded.owner.id == seed_user.id


def test_meetup_has_joined_links_relationship(
    db_session: Session, seed_meetup: Meetup, seed_joined_link: JoinedUsers
) -> None:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    assert seed_joined_link.id in [jl.id for jl in loaded.joined_links]


def test_joined_link_has_user_relationship(
    db_session: Session, seed_joined_link: JoinedUsers, seed_second_user: User
) -> None:
    loaded = db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id)).one()
    assert loaded.user is not None
    assert loaded.user.id == seed_second_user.id


def test_joined_link_has_meetup_relationship(
    db_session: Session, seed_joined_link: JoinedUsers, seed_meetup: Meetup
) -> None:
    loaded = db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id)).one()
    assert loaded.meetup is not None
    assert loaded.meetup.id == seed_meetup.id


def test_joined_link_invited_by_is_none(db_session: Session, seed_joined_link: JoinedUsers) -> None:
    loaded = db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id)).one()
    assert loaded.invited_by is None
    assert loaded.invited_by_id is None
