import pytest

from mitup_bot.config import EnvVariablesConfigProvider

VALID_CONFIG = {
    "group1": {
        "a_string": "Some String",
        "an_int": 123,
    },
    "grouop2": {
        "with_other_string": "what is this?",
        "a_float": 1.23,
        "a_boolean": True,
    },
}


@pytest.mark.parametrize("mock_env_config", (VALID_CONFIG,), indirect=True)
def test_config_loads_properly(mock_env_config):
    config = EnvVariablesConfigProvider().get_config()

    # The config returned from environment variables should be the same as the
    # one provided
    assert VALID_CONFIG == config
