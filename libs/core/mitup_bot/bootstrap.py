"""How a service entry point loads its configuration, and how it reports one it cannot use.

Both services (the bot runtime and the recurrent-events runner) resolve configuration from the same
provider pair and must fail the same way, so the step lives here rather than once per app.
"""

import structlog
from pydantic import ValidationError

from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, TomlConfigProvider, invalid_settings
from mitup_bot.logging_config import Component, configure_logging

log = structlog.get_logger(__name__)


def load_config(env: Env, component: Component) -> Config:
    """Load the process configuration, naming what was wrong before an invalid one raises.

    Both the log level and the release marker are config values, so the pipeline cannot already be
    installed when validation runs. The failure path installs it with its defaults, which turns a
    misconfiguration from a bare traceback on stderr into a line the aggregator indexes under the
    same `component` as everything else this process would have written. The caller installs the
    pipeline for real once it holds the values.

    The rejected settings are reported through `invalid_settings`, and the exception is deliberately
    not attached: a pydantic `ValidationError` renders the input it rejected, which for a malformed
    credential is the credential.
    """
    try:
        return Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(env=env))
    except ValidationError as error:
        configure_logging(env, component)
        log.error(
            "Configuration is invalid",
            env=env.value,
            reason="config_validation_failed",
            settings=invalid_settings(error),
        )
        raise
