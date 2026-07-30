"""Process-wide holder for the Patreon config, injected once during startup.

The live :class:`~mitup_bot.config.PatreonConfig` is injected here at startup via
:func:`configure` and read back through :func:`current_config` from the handler and web layers,
which have no direct config access of their own.
"""

from typing import ClassVar

import structlog

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonNotConfigured

log = structlog.get_logger(__name__)


class PatreonRuntime:
    """Holds the live Patreon config for the process."""

    config: ClassVar[PatreonConfig | None] = None


def configure(config: PatreonConfig):
    """Inject the live Patreon config. Called once at startup.

    The entry points log the resolved Patreon facts as part of their startup narrative, so this
    injection stays silent.
    """
    PatreonRuntime.config = config


def current_config() -> PatreonConfig:
    """Return the configured Patreon section, raising if the entry point never injected one."""
    if PatreonRuntime.config is None:
        log.error("Patreon used before configuration", reason="patreon_not_configured")
        raise PatreonNotConfigured
    return PatreonRuntime.config
