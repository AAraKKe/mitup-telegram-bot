"""Unit coverage for the shared creator-token acquisition (adopt / refresh / persist).

The metric-wrapped ``premium_check.refresh_creator_token`` is exercised in ``tests/cli`` and the live-DB
behavior suite; here we cover the metric-free ``acquire_creator_access_token`` that webhook registration
calls, against the mock session."""

import datetime as dt

import pytest
from sqlmodel import select

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import PatreonCreatorToken
from mitup_bot.patreon import TokenPair
from mitup_bot.patreon.creator_token import acquire_creator_access_token, seed_fingerprint
from tests.helpers import MockDbSession, create_patreon_config, create_patreon_creator_token


@pytest.fixture
def config() -> PatreonConfig:
    return create_patreon_config()


class FakeRefresher:
    """Minimal ``TokenRefresher`` stub: rotates the pair, or raises ``PatreonTokenRevoked`` for a
    refresh token flagged revoked."""

    def __init__(self, *, revoked: frozenset[str] = frozenset()):
        self.revoked = revoked
        self.refresh_calls: list[TokenPair] = []

    async def refresh(self, pair: TokenPair) -> TokenPair:
        self.refresh_calls.append(pair)
        if pair.refresh_token in self.revoked:
            raise PatreonTokenRevoked
        return TokenPair(
            access_token=f"{pair.access_token}-new",
            refresh_token=f"{pair.refresh_token}-new",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
        )


async def test_acquire_adopts_seed_and_persists_when_no_row(mock_session: MockDbSession, config: PatreonConfig):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    client = FakeRefresher()

    access_token = await acquire_creator_access_token(client, config)

    assert access_token == "creator-access-seed-new"
    assert client.refresh_calls[0].refresh_token == "creator-refresh-seed"
    stored = next(obj for obj in mock_session.objects_added if isinstance(obj, PatreonCreatorToken))
    assert stored.access_token == "creator-access-seed-new"
    assert stored.seed_fingerprint == seed_fingerprint(config)


async def test_acquire_refreshes_stored_pair_in_place(mock_session: MockDbSession, config: PatreonConfig):
    row = create_patreon_creator_token(
        access_token="db-access", refresh_token="db-refresh", seed_fingerprint=seed_fingerprint(config)
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    client = FakeRefresher()

    access_token = await acquire_creator_access_token(client, config)

    assert access_token == "db-access-new"
    assert row.access_token == "db-access-new"
    mock_session.assert_not_added()


async def test_acquire_returns_none_on_invalid_grant(mock_session: MockDbSession, config: PatreonConfig):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    client = FakeRefresher(revoked=frozenset({"creator-refresh-seed"}))

    access_token = await acquire_creator_access_token(client, config)

    assert access_token is None
    # A rejected refresh must not persist anything.
    assert not any(isinstance(obj, PatreonCreatorToken) for obj in mock_session.objects_added)
