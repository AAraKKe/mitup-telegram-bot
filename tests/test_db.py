import asyncio
import inspect
from unittest import mock

import pytest
from pydantic import BaseModel
from sqlmodel import Session

from mitup_bot import db
from mitup_bot.config import DbConfig
from mitup_bot.models import Meetup, MeetupLocation
from tests.helpers.stub_db import MockDbSession


@pytest.fixture(autouse=True, scope="function")
def reset_db():
    # Make sure to reset the db configuration after each test so we can
    # validate its behavior
    yield
    db.__sessionmaker = None  # type: ignore


@pytest.fixture(
    params=(
        MeetupLocation(name="Test", coordinates=(123.1, 321.1)),
        MeetupLocation(name="Test"),
        MeetupLocation(coordinates=(123.1, 321.1)),
    ),
    ids=("full_location", "only_name_location", "only_coordinates_location"),
)
def serializable_model(request: pytest.FixtureRequest):
    return request.param


def test_db_initilization(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_engine") as mock_engine,
    ):
        db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(
        db_config.full_url,
        echo=db_config.engine_echo,
        json_serializer=db.serialize_pydantic_model,
        json_deserializer=db.deserialize_pydantic_model,
    )


def test_db_cannot_be_configured_twice(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_engine") as mock_engine,
    ):
        db.configure_db(db_config)

        with pytest.raises(db.DbAlreadyInitializedError):
            db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(
        db_config.full_url,
        echo=db_config.engine_echo,
        json_serializer=db.serialize_pydantic_model,
        json_deserializer=db.deserialize_pydantic_model,
    )


def test_cannot_get_transaction_without_configuring_db():
    with pytest.raises(db.DbNotInitializedError):
        with db.begin():
            pass


def test_decorator_with_async(mock_session: MockDbSession):
    async def f(s: Session) -> int:
        return 1

    wrapped = db.with_async_session(f)()

    assert inspect.iscoroutine(wrapped)

    assert asyncio.run(wrapped) == 1


def test_decorator_with_method(mock_session: MockDbSession):
    def f(s: Session) -> int:
        return 1

    wrapped = db.with_session(f)()

    assert not inspect.iscoroutine(wrapped)

    assert wrapped == 1


def test_engine_json_serializer(meeting: Meetup):
    serialized = db.serialize_pydantic_model(meeting)

    assert serialized == meeting.model_dump_json()


def test_engine_json_deserializer(serializable_model: BaseModel):
    deserialized = db.deserialize_pydantic_model(serializable_model.model_dump_json())

    assert deserialized == serializable_model


def test_json_deserializer_with_non_serializable_model():
    assert db.deserialize_pydantic_model('{"something": "Test"}') is None
