"""Real-Postgres proof of the creator-token adopt/refresh/persist lifecycle in the premium job.

``refresh_creator_token`` drives ``@db.with_session``, which opens and commits its own transactions,
so results are read back through fresh ``db.begin()`` sessions and the committed row is wiped in a
``finally``-guarded transaction between tests. This range owns no ``tg_user_id`` (the creator token is
account-wide), only the single ``patreon_creator_tokens`` row.
"""

import datetime as dt
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.events import supporter_check
from mitup_bot.models import PatreonCreatorToken, configure_token_encryption
from mitup_bot.models.subscriptions import TokenCipher
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import TokenPair
from tests.helpers import create_patreon_config

pytestmark = pytest.mark.db_test


@pytest.fixture(autouse=True)
def configured_db(db_session: AsyncSession) -> AsyncSession:
    """Depend on the session-scoped ``db_session`` so ``configure_db`` has run before
    ``refresh_creator_token`` opens its own ``db.begin`` transactions."""
    return db_session


@pytest.fixture(autouse=True, scope="module")
def configured_token_encryption() -> Iterator[None]:
    saved = TokenCipher.cipher
    configure_token_encryption(Fernet.generate_key().decode())
    try:
        yield
    finally:
        TokenCipher.cipher = saved


@pytest_asyncio.fixture(loop_scope="session")
async def clean_creator_tokens() -> AsyncIterator[None]:
    async def wipe():
        async with db.begin() as session:
            await session.exec(text("DELETE FROM patreon_creator_tokens"))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657

    await wipe()
    try:
        yield
    finally:
        await wipe()


class RotatingClient:
    """Minimal Patreon client that always rotates the pair — enough to exercise persistence."""

    async def refresh(self, pair: TokenPair) -> TokenPair:
        return TokenPair(
            access_token=f"{pair.access_token}-new",
            refresh_token=f"{pair.refresh_token}-new",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
        )


async def read_single_row() -> PatreonCreatorToken:
    async with db.begin() as session:
        return (await session.exec(select(PatreonCreatorToken))).one()


async def test_adopt_persists_encrypted_seed_pair(clean_creator_tokens: None):
    config = create_patreon_config()

    token = await supporter_check.refresh_creator_token(RotatingClient(), config, MetricsClient(NullBackend()))

    assert token == "creator-access-seed-new"
    row = await read_single_row()
    # Decrypted round-trip through the EncryptedToken column matches the rotated seed pair.
    assert row.access_token == "creator-access-seed-new"
    assert row.refresh_token == "creator-refresh-seed-new"
    assert row.seed_fingerprint == supporter_check.seed_fingerprint(config)
    assert row.token_expiration is not None

    # The tokens are ciphertext at rest, not the plaintext we read back above.
    async with db.begin() as session:
        raw_row = (await session.exec(text("SELECT access_token FROM patreon_creator_tokens"))).one()  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
    assert raw_row[0] != "creator-access-seed-new"


async def test_matching_fingerprint_refreshes_db_pair_in_place(clean_creator_tokens: None):
    config = create_patreon_config()
    async with db.begin() as session:
        session.add(
            PatreonCreatorToken(
                access_token="db-access",
                refresh_token="db-refresh",
                token_expiration=dt.datetime.now(dt.UTC),
                seed_fingerprint=supporter_check.seed_fingerprint(config),
            )
        )
        await session.flush()
        original_id = (await session.exec(select(PatreonCreatorToken))).one().db_id

    token = await supporter_check.refresh_creator_token(RotatingClient(), config, MetricsClient(NullBackend()))

    assert token == "db-access-new"
    row = await read_single_row()
    assert row.db_id == original_id  # updated in place, not a second row
    assert row.access_token == "db-access-new"
    assert row.seed_fingerprint == supporter_check.seed_fingerprint(config)


async def test_changed_seed_reseeds_and_rotates_fingerprint(clean_creator_tokens: None):
    config = create_patreon_config()
    async with db.begin() as session:
        session.add(
            PatreonCreatorToken(
                access_token="db-access",
                refresh_token="db-refresh",
                token_expiration=dt.datetime.now(dt.UTC),
                seed_fingerprint="stale-fingerprint",
            )
        )

    token = await supporter_check.refresh_creator_token(RotatingClient(), config, MetricsClient(NullBackend()))

    # The seed changed, so the config pair is adopted and the stored fingerprint rotates to it.
    assert token == "creator-access-seed-new"
    row = await read_single_row()
    assert row.access_token == "creator-access-seed-new"
    assert row.seed_fingerprint == supporter_check.seed_fingerprint(config)
