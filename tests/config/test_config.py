from unittest import mock

import pytest
from pydantic import ValidationError

from mitup_bot.cli.options import Env
from mitup_bot.config import (
    Config,
    EnvVariablesConfigProvider,
    RunModes,
    TomlConfigProvider,
)

TOML_CONTENT = """
[db]
username = "username"
password = "xxxxx"
url      = "some.url.com"
database = "mydb"
port     = 12

[app]
run_mode = "polling"
"""

CONFIG_FROM_ENV = {
    "db": {
        "password": "1234abc",
    },
    "bot": {
        "token": "abcd12345",
    },
}

BROKEN_TOML_CONTENT = """
[db]
url      = "some.url.com"
database = "mydb"
port     = 12

[app]
run_mode = "polling"

[bot]
token = "123123123"
"""


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
)
def test_config_properly_setup(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # Given a valid configuration set from toml and environment we can use the providers to get a config object
    config = Config.from_providers(
        EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV)
    )

    # Password from environment variable takes precedence as it is defined before Toml
    assert "postgresql://username:1234abc@some.url.com:12/mydb" == config.db.full_url
    assert "abcd12345" == config.bot.token.get_secret_value()
    assert RunModes.POLLING is config.app.run_mode


@pytest.mark.parametrize("mock_toml_config", (BROKEN_TOML_CONTENT,), indirect=True)
def test_config_fails_with_missing_values(mock_toml_config: tuple[mock.Mock]):
    # We are just supplying a broken toml where the information for db is not complete.
    # A validation error should be raised
    with pytest.raises(ValidationError) as exc_info:
        Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    # Assert that there are 2 errors for the missing pieces of db
    assert 2 == len(exc_info.value.errors())
    assert "value_error.missing" == exc_info.value.errors()[0]["type"]
    assert "value_error.missing" == exc_info.value.errors()[1]["type"]
    assert ("db", "username") == exc_info.value.errors()[0]["loc"]
    assert ("db", "password") == exc_info.value.errors()[1]["loc"]
    assert "Config" == exc_info.value.model.__name__
