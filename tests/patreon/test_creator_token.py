"""Unit coverage for the shared creator-token acquisition (adopt / refresh / persist).

The metric-wrapped ``supporter_check.refresh_creator_token`` is exercised in ``tests/cli`` and the live-DB
behavior suite; here we cover the metric-free ``acquire_creator_access_token`` that webhook registration
calls, against the mock session."""

import datetime as dt

import pytest
from sqlmodel import select
from structlog.testing import capture_logs
from structlog.typing import EventDict

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


def one_log(logs: list[EventDict], event: str) -> EventDict:
    """Return the single captured structlog entry whose event string matches ``event``."""
    matching = [entry for entry in logs if entry["event"] == event]
    assert len(matching) == 1, f"expected exactly one {event!r} log, got {len(matching)}"
    return matching[0]


async def test_source_selection_names_why_the_seed_won_on_a_fresh_boot(
    mock_session: MockDbSession, config: PatreonConfig
):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())

    with capture_logs() as logs:
        await acquire_creator_access_token(FakeRefresher(), config)

    selected = one_log(logs, "Selected Patreon creator token source")
    assert (selected["source"], selected["reason"]) == ("config_seed", "no_stored_row")
    # The fingerprint is a hash of a live credential, so only its head reaches the line.
    assert selected["seed_fingerprint"] == seed_fingerprint(config)[:12]
    assert one_log(logs, "Stored rotated Patreon creator token")["action"] == "insert"
    assert one_log(logs, "Acquired Patreon creator access token")["source"] == "config_seed"


async def test_source_selection_reports_an_operator_reseed(mock_session: MockDbSession, config: PatreonConfig):
    # The one decision that says whether a re-seed took effect: a stored row whose fingerprint no
    # longer matches config is abandoned in favour of the freshly seeded pair.
    row = create_patreon_creator_token(access_token="db-access", refresh_token="db-refresh", seed_fingerprint="stale")
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    # The row is rotated in place, so the expiry both lines report has to be read before the write.
    superseded_expiration = row.token_expiration

    with capture_logs() as logs:
        await acquire_creator_access_token(FakeRefresher(), config)

    selected = one_log(logs, "Selected Patreon creator token source")
    assert (selected["source"], selected["reason"]) == ("config_seed", "seed_fingerprint_changed")
    assert selected["stored_expires_at"] == superseded_expiration
    stored = one_log(logs, "Stored rotated Patreon creator token")
    assert (stored["action"], stored["previous_expires_at"]) == ("update", superseded_expiration)


async def test_source_selection_keeps_the_stored_pair_when_the_fingerprint_matches(
    mock_session: MockDbSession, config: PatreonConfig
):
    row = create_patreon_creator_token(
        access_token="db-access", refresh_token="db-refresh", seed_fingerprint=seed_fingerprint(config)
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))

    with capture_logs() as logs:
        await acquire_creator_access_token(FakeRefresher(), config)

    selected = one_log(logs, "Selected Patreon creator token source")
    assert (selected["source"], selected["reason"]) == ("database", "stored_fingerprint_matches")


async def test_a_rejected_refresh_names_the_source_and_the_recovery(mock_session: MockDbSession, config: PatreonConfig):
    row = create_patreon_creator_token(
        access_token="db-access", refresh_token="db-refresh", seed_fingerprint=seed_fingerprint(config)
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))

    with capture_logs() as logs:
        assert await acquire_creator_access_token(FakeRefresher(revoked=frozenset({"db-refresh"})), config) is None

    rejected = one_log(logs, "Patreon creator token refresh rejected, re-seed required")
    assert (rejected["reason"], rejected["source"]) == ("invalid_grant", "database")
    assert rejected["remediation"] == "reseed_from_developer_portal"
    assert rejected["stored_expires_at"] == row.token_expiration


async def test_no_creator_token_material_reaches_the_log_plane(mock_session: MockDbSession, config: PatreonConfig):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())

    with capture_logs() as logs:
        await acquire_creator_access_token(FakeRefresher(), config)

    rendered = repr(logs)
    assert config.creator_access_token.get_secret_value() not in rendered
    assert config.creator_refresh_token.get_secret_value() not in rendered
    assert seed_fingerprint(config) not in rendered
