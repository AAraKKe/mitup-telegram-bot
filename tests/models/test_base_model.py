import datetime as dt
from unittest import mock

import pytest
from freezegun import freeze_time
from pydantic import SecretStr
from sqlmodel import SQLModel

from mitup_bot.config import DbConfig
from mitup_bot.models import MitupBaseModel
from mitup_bot.models.exceptions import MissingSessionError


class BaseModelImpl(MitupBaseModel, SQLModel):
    name: str
    updated_time: dt.datetime | None = None


def test_engine_generated_properly():
    config = DbConfig(username="user", password=SecretStr("password"), url="url", database="db")

    with mock.patch("mitup_bot.models.mitup_base_model.create_engine") as create_engine_mock:
        create_engine_mock.return_value = mock.sentinel.engine
        MitupBaseModel.set_engine(config)

    assert MitupBaseModel._engine is mock.sentinel.engine
    create_engine_mock.assert_called_once_with(config.full_url, echo=False)


def test_create(mock_session: mock.MagicMock):
    with BaseModelImpl.open_session():
        impl = BaseModelImpl(name="test")
        impl.create()

    # Add and commit has been called
    mock_session.add.assert_called_with(impl)
    mock_session.commit.assert_called_once()


@freeze_time(dt.datetime(2023, 11, 20, 12, 12, tzinfo=dt.UTC))
def test_update(mock_session: mock.MagicMock):
    with BaseModelImpl.open_session():
        impl = BaseModelImpl(name="test")
        impl.update()

    # Add and commit has been called
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

    # The updated_time of the settings used as first argument on the first call is now
    added_settings: BaseModelImpl = mock_session.add.call_args_list[0].args[0]
    assert dt.datetime.now(dt.UTC) == added_settings.updated_time


def test_create_fails_without_session():
    impl = BaseModelImpl(name="test")

    with pytest.raises(MissingSessionError):
        impl.create()


def test_update_fails_without_session():
    impl = BaseModelImpl(name="test")

    with pytest.raises(MissingSessionError):
        impl.update()
