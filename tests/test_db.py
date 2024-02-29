import asyncio
import inspect
from unittest import mock

import pytest
from sqlmodel import Session

from mitup_bot import db
from mitup_bot.config import DbConfig


@pytest.fixture(autouse=True, scope="function")
def reset_db():
    # Make sure to reset the db configuration after each test so we can
    # validate its behavior
    yield
    db.__sessionmaker = None  # type: ignore


def test_db_initilization(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_engine") as mock_engine,
    ):
        db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(db_config.full_url, echo=db_config.engine_echo)


def test_db_cannot_be_configured_twice(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_engine") as mock_engine,
    ):
        db.configure_db(db_config)

        with pytest.raises(db.DbAlreadyInitializedError):
            db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(db_config.full_url, echo=db_config.engine_echo)


def test_cannot_get_transaction_without_configuring_db():
    with pytest.raises(db.DbNotInitializedError):
        with db.begin():
            pass


def test_decorator_with_async(mock_session: mock.MagicMock):
    async def f(s: Session) -> int:
        return 1

    wrapped = db.with_async_session(f)()

    assert inspect.iscoroutine(wrapped)

    assert asyncio.run(wrapped) == 1


def test_decorator_with_method(mock_session: mock.MagicMock):
    def f(s: Session) -> int:
        return 1

    wrapped = db.with_session(f)()

    assert not inspect.iscoroutine(wrapped)

    assert wrapped == 1
