from unittest import mock

from pydantic import SecretStr

from mitup_bot.config import DbConfig
from mitup_bot.models import MitupBaseModel


def test_engine_generated_properly():
    config = DbConfig(
        username="user", password=SecretStr("password"), url="url", database="db"
    )

    with mock.patch(
        "mitup_bot.models.mitup_base_model.create_engine"
    ) as create_engine_mock:
        create_engine_mock.return_value = mock.sentinel.engine
        MitupBaseModel.set_engine(config)

    assert MitupBaseModel.engine is mock.sentinel.engine
    create_engine_mock.assert_called_once_with(config.full_url, echo=False)
