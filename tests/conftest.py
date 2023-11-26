import pytest
from pydantic import SecretStr

from mitup_bot.config import DbConfig


@pytest.fixture(scope="session")
def db_config() -> DbConfig:
    return DbConfig(
        username="user",
        password=SecretStr("password"),
        url="testhost",
        database="db",
    )
