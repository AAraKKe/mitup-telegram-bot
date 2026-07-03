from unittest import mock

import pytest
from pydantic import ValidationError
from sqlalchemy import URL

from mitup_bot.config import AppConfig, Config, Env, EnvVariablesConfigProvider, RunModes, TomlConfigProvider

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
        drivername="postgresql+psycopg",
        username="username",
        password="1234abc",
        host="some.url.com",
        port=12,
        database="mydb",
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


def test_app_config_log_level_defaults_to_info():
    config = AppConfig(run_mode=RunModes.POLLING)

    assert config.log_level == "INFO"


def test_app_config_log_level_normalized_to_upper():
    config = AppConfig(run_mode=RunModes.POLLING, log_level="debug")

    assert config.log_level == "DEBUG"


def test_app_config_invalid_log_level_raises():
    with pytest.raises(ValidationError) as exc_info:
        AppConfig(run_mode=RunModes.POLLING, log_level="bogus")

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("log_level",)


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["working_config"],
)
def test_config_without_log_level_still_builds(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # Backwards-compat: TOML_CONTENT does not set [app] log_level, so the default must apply.
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.app.log_level == "INFO"
