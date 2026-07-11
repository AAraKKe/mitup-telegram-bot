from unittest import mock

import pytest

from mitup_bot.app import MitupRuntime
from mitup_bot.config import Env
from tests.helpers import CliRunner


@pytest.mark.parametrize(
    "env",
    ("dev", "prod", "sample", "DEV", "PROD", "SAMPLE"),
    ids=["lowercase_dev", "lowercase_prod", "lowercase_sample", "uppercase_dev", "uppercase_prod", "uppercase_sample"],
)
def test_launch_valid_environment(env: Env, cli: CliRunner):
    with mock.patch("mitup_bot.bot_cli.MitupRuntime", spec=MitupRuntime, name="MockedRuntime") as runtime:
        runtime_obj = mock.MagicMock()
        runtime.return_value = runtime_obj
        cli(f"launch --env {env}")

    expected_env = Env[env.upper()]
    runtime_obj.run.assert_called_once()
    runtime.assert_called_once_with(expected_env)


def test_launch_invalid_environment(cli: CliRunner):
    result = cli("launch --env invalid_env")
    assert result.exit_code != 0
    assert "Invalid value for '--env'" in result.output
