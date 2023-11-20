from unittest import mock

import pytest

from mitup_bot import environments
from mitup_bot.cli.options import Env
from mitup_bot.config import TomlConfigProvider

TOML_CONTENT = """
[table1]
some_value = "string 1"
a_number = 123

[other_section]
with_other_string = "what is this?"
"""


@pytest.mark.parametrize("mock_toml_config", (TOML_CONTENT,), indirect=True)
def test_config_map_is_parsed(mock_toml_config: tuple[mock.Mock, mock.Mock]):
    provider = TomlConfigProvider(Env.DEV)

    config = provider.get_config()

    expected_config = {
        "table1": {
            "some_value": "string 1",
            "a_number": 123,
        },
        "other_section": {"with_other_string": "what is this?"},
    }
    # Check the content is valid
    assert expected_config == config

    # Check he expected file has been read
    mock_toml_config[0].assert_called_with(environments)


@pytest.mark.parametrize("mock_toml_config", (TOML_CONTENT,), indirect=True)
def test_empty_config_on_error(mock_toml_config: tuple[mock.Mock, mock.Mock]):
    provider = TomlConfigProvider(Env.DEV)

    # When calling files we raise a RuntimeError
    mock_toml_config[0].side_effect = RuntimeError

    config = provider.get_config()

    # Check the content is valid
    assert {} == config

    # Check he expected file has been read
    mock_toml_config[0].assert_called_with(environments)
