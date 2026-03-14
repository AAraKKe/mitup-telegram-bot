import datetime as dt
from collections.abc import Callable

import pytest
from sqlmodel import Session, select

from mitup_bot.models import Meetup, Settings, User

pytestmark = pytest.mark.db_test


# --- INSERT path helpers ---


def _user_timestamps(
    db_session: Session, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = db_session.exec(select(User).where(User.id == seed_user.id)).one()
    db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


def _settings_timestamps(
    db_session: Session, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = db_session.exec(select(Settings).where(Settings.user_id == seed_user.id)).one()
    db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


def _meetup_timestamps(
    db_session: Session, seed_user: User, seed_meetup: Meetup
) -> tuple[dt.datetime | None, dt.datetime | None]:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    db_session.refresh(loaded)
    return loaded.created_time, loaded.updated_time


@pytest.mark.parametrize(
    "get_timestamps",
    [_user_timestamps, _settings_timestamps, _meetup_timestamps],
    ids=["users", "settings", "meetups"],
)
def test_timestamps_are_set_on_insert(
    db_session: Session,
    seed_user: User,
    seed_meetup: Meetup,
    get_timestamps: Callable[[Session, User, Meetup], tuple[dt.datetime | None, dt.datetime | None]],
) -> None:
    created_time, updated_time = get_timestamps(db_session, seed_user, seed_meetup)
    assert created_time is not None
    assert isinstance(created_time, dt.datetime)
    assert updated_time is not None
    assert isinstance(updated_time, dt.datetime)


# --- UPDATE path helpers ---


def _update_user(db_session: Session, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = db_session.exec(select(User).where(User.id == seed_user.id)).one()
    loaded.last_name = "trigger-check"
    db_session.flush()
    db_session.refresh(loaded)
    return loaded.updated_time


def _update_settings(db_session: Session, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = db_session.exec(select(Settings).where(Settings.user_id == seed_user.id)).one()
    loaded.timezone = "Europe/Madrid"
    db_session.flush()
    db_session.refresh(loaded)
    return loaded.updated_time


def _update_meetup(db_session: Session, seed_user: User, seed_meetup: Meetup) -> dt.datetime | None:
    loaded = db_session.exec(select(Meetup).where(Meetup.id == seed_meetup.id)).one()
    loaded.description = "trigger-check"
    db_session.flush()
    db_session.refresh(loaded)
    return loaded.updated_time


@pytest.mark.parametrize(
    "do_update",
    [_update_user, _update_settings, _update_meetup],
    ids=["users", "settings", "meetups"],
)
def test_updated_time_is_set_after_update(
    db_session: Session,
    seed_user: User,
    seed_meetup: Meetup,
    do_update: Callable[[Session, User, Meetup], dt.datetime | None],
) -> None:
    updated_time = do_update(db_session, seed_user, seed_meetup)
    assert updated_time is not None
    assert isinstance(updated_time, dt.datetime)
