import pytest
from sqlmodel import Session, select

from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test


def test_user_persisted(db_session: Session, seed_user: User) -> None:
    loaded = db_session.exec(select(User).where(User.tg_user_id == 999_001)).one()
    assert loaded.first_name == "Seed User One"
    assert loaded.id is not None


def test_settings_created_for_user(db_session: Session, seed_user: User) -> None:
    loaded = db_session.exec(select(Settings).where(Settings.user_id == seed_user.id)).one()
    assert loaded.id is not None
    assert loaded.language == "en"
    assert loaded.timezone == "UTC"


def test_meetup_persisted(db_session: Session, seed_meetup: Meetup) -> None:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    assert loaded.title == "Seed Meetup"
    assert loaded.owner_id == seed_meetup.owner_id


def test_joined_link_persisted(db_session: Session, seed_joined_link: JoinedUsers) -> None:
    loaded = db_session.exec(select(JoinedUsers).where(JoinedUsers.id == seed_joined_link.id)).one()
    assert loaded.user_id is not None
    assert loaded.meetup_id is not None


def test_meetup_location_defaults_empty(db_session: Session, seed_meetup: Meetup) -> None:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    assert loaded.location is not None
    assert loaded.location.name is None
    assert loaded.location.coordinates is None
