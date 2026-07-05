"""Process-wide holder for the optional Patreon config, injected once during startup.

Patreon config is optional (the bot boots without a ``[patreon]`` section), so the live
:class:`~mitup_bot.config.PatreonConfig` is injected here at startup via :func:`configure` and read
back through :func:`current_config` / :func:`is_configured` from the handler and web layers, which
have no direct config access of their own. Mirrors the configure-at-startup pattern used by
``db.configure_db`` and the token cipher.
"""

from typing import ClassVar

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonNotConfigured


class PatreonRuntime:
    """Holds the live Patreon config for the process, or ``None`` when Patreon is unconfigured."""

    config: ClassVar[PatreonConfig | None] = None


def configure(config: PatreonConfig):
    """Inject the live Patreon config. Called once at startup when a ``[patreon]`` section exists."""
    PatreonRuntime.config = config


def is_configured() -> bool:
    """Whether Patreon support is available in this deployment."""
    return PatreonRuntime.config is not None


def current_config() -> PatreonConfig:
    """Return the configured Patreon section, raising if the bot booted without one."""
    if PatreonRuntime.config is None:
        raise PatreonNotConfigured
    return PatreonRuntime.config
