"""Creator-token acquisition, shared by the daily membership job and startup webhook registration.

The creator OAuth pair (campaign-wide access, distinct from the per-user tokens) is seeded from config
and then rotated into ``PatreonCreatorToken`` by whichever process refreshes it first. Both the daily
``supporter_check`` job and the startup webhook registration need a *fresh* creator access token, so the
adopt-or-refresh lifecycle lives here in the Patreon domain. The job wraps ``load``/``store`` with
its TTL/fault metrics; registration only needs a token, so it calls
:func:`acquire_creator_access_token`.
"""

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import PatreonCreatorToken
from mitup_bot.patreon.client import TokenPair

log = structlog.get_logger(__name__)


class TokenRefresher(Protocol):
    """The one Patreon capability the token helpers depend on — narrowed so tests can drive them
    with a stub. :class:`~mitup_bot.patreon.PatreonClient` satisfies it structurally."""

    async def refresh(self, pair: TokenPair) -> TokenPair: ...


@dataclass(frozen=True, slots=True)
class CreatorState:
    """The creator token pair to refresh this run plus the fingerprint to persist alongside it.

    ``fallback_expiration`` is the stored row's expiry (or ``None`` for a fresh adopt) used to emit
    the TTL metric when the refresh itself fails and no new expiry is available."""

    pair: TokenPair
    fingerprint: str
    fallback_expiration: dt.datetime | None


def seed_fingerprint(config: PatreonConfig) -> str:
    """SHA-256 of the configured seed access token, used to detect an operator re-seed."""
    seed = config.creator_access_token.get_secret_value()
    return hashlib.sha256(seed.encode()).hexdigest()


@db.with_session
async def load_creator_state(session: AsyncSession, config: PatreonConfig, fingerprint: str) -> CreatorState:
    """Decide which creator pair refreshes this run: the config seed on first boot or an operator
    re-seed (fingerprint absent or changed), otherwise the DB pair, which is fresher than the seed."""
    row = (await session.exec(select(PatreonCreatorToken))).first()
    if row is None or row.seed_fingerprint != fingerprint:
        seed_pair = TokenPair(
            access_token=config.creator_access_token.get_secret_value(),
            refresh_token=config.creator_refresh_token.get_secret_value(),
            # The seed carries no expiry; refresh only reads the refresh token, so a placeholder is fine.
            expires_at=dt.datetime.now(dt.UTC),
        )
        return CreatorState(
            pair=seed_pair, fingerprint=fingerprint, fallback_expiration=row.token_expiration if row else None
        )
    stored_pair = TokenPair(
        access_token=row.access_token, refresh_token=row.refresh_token, expires_at=row.token_expiration
    )
    return CreatorState(pair=stored_pair, fingerprint=row.seed_fingerprint, fallback_expiration=row.token_expiration)


@db.with_session
async def store_creator_token(session: AsyncSession, pair: TokenPair, fingerprint: str):
    """Persist the rotated creator pair (Fernet-encrypted by the column) before it is used.

    Patreon invalidates the old pair the moment it issues a new one, so this commit must land before
    the fresh access token drives the member sweep."""
    row = (await session.exec(select(PatreonCreatorToken))).first()
    if row is None:
        session.add(
            PatreonCreatorToken(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                token_expiration=pair.expires_at,
                seed_fingerprint=fingerprint,
            )
        )
        return
    row.access_token = pair.access_token
    row.refresh_token = pair.refresh_token
    row.token_expiration = pair.expires_at
    row.seed_fingerprint = fingerprint


async def acquire_creator_access_token(client: TokenRefresher, config: PatreonConfig) -> str | None:
    """Adopt-or-refresh the creator token, persist the rotated pair, and return a fresh access token.

    Returns ``None`` when Patreon rejects the refresh with ``invalid_grant``: that cannot be auto-healed
    (recovery is re-seeding the credential from the developer portal), so it logs an error and lets the
    caller treat it as "no creator token available" rather than raising. A caller that only needs a
    token (webhook registration) simply no-ops."""
    state = await load_creator_state(config, seed_fingerprint(config))
    try:
        pair = await client.refresh(state.pair)
    except PatreonTokenRevoked:
        log.error("Patreon creator token refresh rejected with invalid_grant, re-seed required")
        return None
    await store_creator_token(pair, state.fingerprint)
    return pair.access_token
