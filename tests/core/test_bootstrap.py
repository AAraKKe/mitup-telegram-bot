import logging
from collections.abc import Mapping
from unittest import mock

import pytest
from pydantic import ValidationError

from mitup_bot.bootstrap import load_config
from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.logging_config import Component
from tests.helpers.logs import log_record

# A payload rejected for two different reasons at once: a bot token that arrived from the env
# provider as an int (digits-only values are coerced there), and a section pydantic never reaches.
INVALID_CONFIG = {"bot": {"token": 12345678901234567890}, "app": {"run_mode": "polling"}}


def validation_error(data: Mapping[str, object]) -> ValidationError:
    """The real `ValidationError` `Config` raises for *data*, rather than a hand-built stand-in."""
    with pytest.raises(ValidationError) as error:
        Config.model_validate(data)
    return error.value


@pytest.mark.parametrize("env", [Env.DEV, Env.PROD], ids=["dev", "prod"])
def test_config_is_loaded_from_the_env_vars_and_the_env_toml(env: Env):
    with mock.patch("mitup_bot.bootstrap.Config.from_providers") as from_providers:
        assert load_config(env, Component.BOT) is from_providers.return_value

    providers = from_providers.call_args.args
    # Env vars first: the merge gives the first provider priority, which is what lets a deployed
    # MITUPBOT__* override the checked-in TOML.
    assert isinstance(providers[0], EnvVariablesConfigProvider)
    assert isinstance(providers[1], TomlConfigProvider)
    assert providers[1].env is env


def test_invalid_config_is_narrated_through_the_pipeline_before_it_raises(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.ERROR)
    error = validation_error(INVALID_CONFIG)

    with (
        mock.patch("mitup_bot.bootstrap.Config.from_providers", side_effect=error),
        # Stubbed so the suite's capture pipeline survives: the production call this stands in for
        # replaces the root handlers, and the assertion here is that it ran at all, before the line.
        mock.patch("mitup_bot.bootstrap.configure_logging") as configure,
        pytest.raises(ValidationError),
    ):
        load_config(Env.PROD, Component.EVENTS)

    # Defaults, because the level and the release marker are values of the config that just failed.
    configure.assert_called_once_with(Env.PROD, Component.EVENTS)
    record = log_record(caplog, "Configuration is invalid")
    assert record.__dict__["reason"] == "config_validation_failed"
    assert record.__dict__["env"] == Env.PROD.value
    assert "bot.token: string_type" in record.__dict__["settings"]


def test_the_rejected_value_never_reaches_the_line(caplog: pytest.LogCaptureFixture):
    """A malformed credential is still a credential.

    Pydantic puts the rejected input in both the error message and the `input` key — and for a
    missing section, that input is the *whole* config payload, token included. So the line carries
    the setting paths and error types only, and the exception is deliberately not attached.
    """
    caplog.set_level(logging.ERROR)
    token = str(INVALID_CONFIG["bot"]["token"])  # type: ignore[index]

    with (
        mock.patch("mitup_bot.bootstrap.Config.from_providers", side_effect=validation_error(INVALID_CONFIG)),
        mock.patch("mitup_bot.bootstrap.configure_logging"),
        pytest.raises(ValidationError),
    ):
        load_config(Env.PROD, Component.BOT)

    record = log_record(caplog, "Configuration is invalid")
    assert token not in str(record.__dict__["settings"])
    assert token not in caplog.text
    assert record.exc_info is None
