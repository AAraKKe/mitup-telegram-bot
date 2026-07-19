"""Patreon integration.

The moving parts live in submodules so nothing but re-exports sits in this file:

- :mod:`mitup_bot.patreon.client` — the async HTTP client and its domain value types.
- :mod:`mitup_bot.patreon.models` — pydantic models for the Patreon API responses.
- :mod:`mitup_bot.patreon.oauth` — Fernet-signed ``state`` handling and URL construction.
- :mod:`mitup_bot.patreon.runtime` — the process-wide config holder.
"""

from mitup_bot.patreon import oauth
from mitup_bot.patreon.client import PatreonClient, TokenPair
from mitup_bot.patreon.models import IdentityResponse, MemberResource
from mitup_bot.patreon.runtime import PatreonRuntime, configure, current_config

__all__ = [
    "IdentityResponse",
    "MemberResource",
    "PatreonClient",
    "PatreonRuntime",
    "TokenPair",
    "configure",
    "current_config",
    "oauth",
]
