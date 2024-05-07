from unittest import mock

import pytest
from pydantic import ValidationError
from sqlalchemy import URL

from mitup_bot.cli.options import Env
from mitup_bot.config import Config, EnvVariablesConfigProvider, RunModes, TomlConfigProvider

TOML_CONTENT = """
[db]
username = "username"
password = "xxxxx"
url      = "some.url.com"
database = "mydb"
port     = 12

[app]
run_mode = "polling"

[metrics]
namespace = "MitupBot"
environment = "local"
log_group = "LogGroup"
"""

CONFIG_FROM_ENV = {
    "db": {
        "password": "1234abc",
    },
    "bot": {
        "token": "abcd12345",
    },
    "google_api": {
        "gmaps_geocode_key": "1a2b3c45d6e7f8g",
        "gmaps_timezone_key": "9h0i1j2k3l4m5n6o",
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

[google_api]
gmaps_geocode_key = "1a2b3c45d6e7f8g"

[metrics]
environment = "broken"
namespace = "MitupBot"
"""


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["working_config"],
)
def test_config_properly_setup(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # Given a valid configuration set from toml and environment we can use the providers to get a config object
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    # Password from environment variable takes precedence as it is defined before Toml
    expected_url = URL.create(
        drivername="postgresql", username="username", password="1234abc", host="some.url.com", port=12, database="mydb"
    )
    assert config.db.full_url == expected_url
    assert config.bot.token.get_secret_value() == "abcd12345"
    assert config.google_api.gmaps_geocode_key.get_secret_value() == "1a2b3c45d6e7f8g"
    assert config.google_api.gmaps_timezone_key.get_secret_value() == "9h0i1j2k3l4m5n6o"
    assert RunModes.POLLING is config.app.run_mode


@pytest.mark.parametrize("mock_toml_config", (BROKEN_TOML_CONTENT,), indirect=True, ids=["broken_config"])
def test_config_fails_with_missing_values(mock_toml_config: tuple[mock.Mock]):
    # We are just supplying a broken toml where the information for db is not complete.
    # A validation error should be raised
    with pytest.raises(ValidationError) as exc_info:
        Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    # Assert that there are 2 errors for the missing pieces of db
    assert len(exc_info.value.errors()) == 4
    assert exc_info.value.errors()[0]["type"] == "missing"
    assert exc_info.value.errors()[1]["type"] == "missing"
    assert exc_info.value.errors()[2]["type"] == "missing"
    assert exc_info.value.errors()[3]["type"] == "enum"
    assert exc_info.value.errors()[0]["loc"] == ("db", "username")
    assert exc_info.value.errors()[1]["loc"] == ("db", "password")
    assert exc_info.value.errors()[2]["loc"] == ("google_api", "gmaps_timezone_key")
    assert exc_info.value.errors()[3]["loc"] == ("metrics", "environment")

    assert exc_info.value.title == "Config"
