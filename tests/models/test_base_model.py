from unittest import mock

import pytest
from pydantic import SecretStr
from sqlmodel import SQLModel

from mitup_bot.config import DbConfig
from mitup_bot.models import MitupBaseModel
from mitup_bot.models.exceptions import MissingSessionError


class BaseModelImpl(MitupBaseModel, SQLModel):
    name: str


def test_engine_generated_properly():
    config = DbConfig(
        username="user", password=SecretStr("password"), url="url", database="db"
    )

    with mock.patch(
        "mitup_bot.models.mitup_base_model.create_engine"
    ) as create_engine_mock:
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


def test_create_failes_without_session(mock_session: mock.MagicMock):
    impl = BaseModelImpl(name="test")

    with pytest.raises(MissingSessionError):
        impl.create()
