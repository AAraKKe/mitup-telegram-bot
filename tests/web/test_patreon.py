import contextlib
import datetime as dt
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from freezegun import freeze_time

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig, RunModes
from mitup_bot.exceptions import PatreonApiError
from mitup_bot.models import PremiumSubscription
from mitup_bot.patreon import PatreonRuntime, TokenPair, oauth
from mitup_bot.patreon.models import IdentityData, IdentityResponse
from mitup_bot.web import patreon as web_patreon
from mitup_bot.web.patreon import LinkOutcome, link_patreon_account, upsert_subscription
from tests.helpers import (
    MockApi,
    build_ptb_app_mock,
    build_test_web_app,
    build_web_client,
    create_patreon_config,
    create_premium_subscription,
    create_user,
)
from tests.helpers.stub_db import MockDbSession

BOT_USERNAME = "MitupTestBot"


@pytest.fixture
def patreon_config() -> Iterator[PatreonConfig]:
    saved = PatreonRuntime.config
    config = create_patreon_config()
    patreon.configure(config)
    try:
        yield config
    finally:
        PatreonRuntime.config = saved


@pytest.fixture(autouse=True)
def reset_patreon() -> Iterator[None]:
    saved = PatreonRuntime.config
    PatreonRuntime.config = None
    try:
        yield
    finally:
        PatreonRuntime.config = saved


@pytest.fixture
def ptb_app() -> MagicMock:
    app = build_ptb_app_mock()
    app.bot.username = BOT_USERNAME
    return app


@pytest.fixture
def web_app(ptb_app: MagicMock) -> FastAPI:
    return build_test_web_app(ptb_app=ptb_app, run_mode=RunModes.WEBHOOK)


class FakePatreonClient:
    """Stand-in for PatreonClient that skips the network; exchange is configurable. The success
    tests mock link_patreon_account, so the identity content is not asserted here."""

    identity = IdentityResponse(data=IdentityData(id="patreon-1"))
    exchange_error: Exception | None = None

    def __init__(self, config: object, *, transport: object = None):
        pass

    async def __aenter__(self) -> FakePatreonClient:
        return self

    async def __aexit__(self, *exc_info: object):
        pass

    async def exchange_code(self, code: str) -> TokenPair:
        if self.exchange_error is not None:
            raise self.exchange_error
        return TokenPair("access", "refresh", dt.datetime.now(dt.UTC))

    async def fetch_identity(self, access_token: str) -> IdentityResponse:
        return self.identity


async def test_callback_unconfigured_returns_service_unavailable(web_app: FastAPI):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c", "state": "s"})

    assert response.status_code == 503
    assert "isn't switched on yet" in response.text


async def test_callback_denied_consent_is_non_accusative(web_app: FastAPI, patreon_config: PatreonConfig):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"error": "access_denied"})

    assert response.status_code == 400
    assert "not approved" in response.text.lower()
    assert "nothing has changed on your mitup account" in response.text.lower()
    assert f"https://t.me/{BOT_USERNAME}" in response.text


async def test_callback_other_patreon_error_locates_failure_on_patreon(web_app: FastAPI, patreon_config: PatreonConfig):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"error": "server_error"})

    assert response.status_code == 502
    assert "on patreon's side, not yours" in response.text.lower()


async def test_callback_missing_params_reassures_and_names_button(web_app: FastAPI, patreon_config: PatreonConfig):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback")

    assert response.status_code == 400
    assert "incomplete" in response.text.lower()
    assert "you haven't done anything wrong" in response.text.lower()
    assert "Link Patreon account in the Collaborate menu" in response.text


async def test_callback_expired_state_prompts_retry(web_app: FastAPI, patreon_config: PatreonConfig):
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(patreon_config, 997_620)

    with freeze_time("2026-07-05 12:20:00"):
        async with build_web_client(web_app) as client:
            response = await client.get("/patreon/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert "expired" in response.text.lower()
    assert "Link Patreon account in the Collaborate menu" in response.text


async def test_callback_invalid_state_reassures_user(web_app: FastAPI, patreon_config: PatreonConfig):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c", "state": "tampered-token"})

    assert response.status_code == 400
    assert "couldn't verify this link" in response.text.lower()
    assert "you haven't done anything wrong" in response.text.lower()


async def test_callback_patreon_error_renders_retry(
    web_app: FastAPI, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    FakePatreonClient.exchange_error = PatreonApiError("boom")
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config, 997_621)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c", "state": state})

    FakePatreonClient.exchange_error = None
    assert response.status_code == 502


@pytest.mark.parametrize(
    "outcome, status, needle",
    [
        (LinkOutcome.LINKED_PREMIUM, 200, "all set"),
        (LinkOutcome.LINKED_NO_PATRON, 200, "connected"),
        (LinkOutcome.UNKNOWN_USER, 400, "couldn't find your mitup account"),
        (LinkOutcome.ALREADY_LINKED_ELSEWHERE, 409, "already linked"),
    ],
)
async def test_callback_outcome_pages_render_expected_content(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcome: LinkOutcome,
    status: int,
    needle: str,
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    link_mock = AsyncMock(return_value=outcome)
    monkeypatch.setattr(web_patreon, "link_patreon_account", link_mock)
    state = oauth.encode_state(patreon_config, 997_622)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    assert response.status_code == status
    assert needle in response.text.lower()
    link_mock.assert_awaited_once()


# --- Branded template rendering (the pages are filled from templates/patreon_result.html) ---


def test_render_result_page_fills_branded_template():
    response = web_patreon.render_result_page("A Title", "A message body.", "MitupBot")

    body = bytes(response.body).decode()
    assert response.status_code == 200
    assert response.media_type == "text/html"
    # Title and message are substituted into the Mitup-branded shell.
    assert "<h1>A Title</h1>" in body
    assert "A message body." in body
    # The shell carries the Mitup wordmark and the return link to the bot.
    assert 'class="wordmark"' in body
    assert "mitup" in body.lower()
    assert '<a class="cta" href="https://t.me/MitupBot">Open Mitup</a>' in body


def test_render_result_page_omits_return_link_without_username():
    response = web_patreon.render_result_page("T", "M", None)

    body = bytes(response.body).decode()
    assert 'class="cta"' not in body
    assert "<h1>T</h1>" in body


# --- Unit-level coverage of the persistence helpers (mock session, no live DB) ---
# The db_behavior suite proves these against real Postgres, but those tests are db-gated and don't
# feed the unit coverage job; these mock-session tests exercise the same branches for coverage.


def link_pair() -> TokenPair:
    return TokenPair("new-access", "new-refresh", dt.datetime.now(dt.UTC) + dt.timedelta(days=30))


@pytest.fixture
def patch_begin_write(monkeypatch: pytest.MonkeyPatch) -> Callable[[MockDbSession], None]:
    """Return a helper that swaps ``db.begin_write`` for one yielding the given mock session, so
    ``link_patreon_account`` runs its body against the stub instead of a real transaction."""

    def patch(session: MockDbSession):
        @contextlib.asynccontextmanager
        async def fake_begin_write(api: object):
            yield session

        monkeypatch.setattr("mitup_bot.db.begin_write", fake_begin_write)

    return patch


async def test_link_new_patron_grants_premium(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_650)
    session.add_object(user, "tg_user_id")
    patch_begin_write(session)

    api = MockApi()
    outcome = await link_patreon_account(api, 997_650, link_pair(), patreon_user_id="p-650", is_active_member=True)

    assert outcome is LinkOutcome.LINKED_PREMIUM
    assert user.is_premium is True
    added = [obj for obj in session.objects_added if isinstance(obj, PremiumSubscription)]
    assert len(added) == 1
    assert added[0].patreon_user_id == "p-650"
    assert added[0].premium_expiration is not None
    api.assert_method_just_called("send_message_to_user", times=1)


async def test_link_new_non_patron_stores_without_premium(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_651)
    session.add_object(user, "tg_user_id")
    patch_begin_write(session)

    api = MockApi()
    outcome = await link_patreon_account(api, 997_651, link_pair(), patreon_user_id="p-651", is_active_member=False)

    assert outcome is LinkOutcome.LINKED_NO_PATRON
    assert user.is_premium is False
    added = [obj for obj in session.objects_added if isinstance(obj, PremiumSubscription)]
    assert len(added) == 1
    assert added[0].premium_expiration is None
    api.assert_method_just_called("send_message_to_user", times=1)


async def test_link_unknown_user_returns_unknown(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()  # the user is intentionally not registered
    patch_begin_write(session)

    api = MockApi()
    outcome = await link_patreon_account(api, 997_659, link_pair(), patreon_user_id="p-659", is_active_member=True)

    assert outcome is LinkOutcome.UNKNOWN_USER
    assert not session.objects_added
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_link_rejected_when_account_claimed_elsewhere(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_652)
    session.add_object(user, "tg_user_id")
    # A subscription for the same Patreon account already belongs to a different user.
    other = create_premium_subscription(user_id=2, patreon_user_id="p-shared")
    session.add_object(other, "patreon_user_id")
    patch_begin_write(session)

    api = MockApi()
    outcome = await link_patreon_account(api, 997_652, link_pair(), patreon_user_id="p-shared", is_active_member=True)

    assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
    assert user.is_premium is False
    assert not any(isinstance(obj, PremiumSubscription) for obj in session.objects_added)
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_upsert_creates_subscription_when_absent():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_653)

    subscription = await upsert_subscription(session, user, link_pair(), "p-653")

    assert subscription in session.objects_added
    assert subscription.user_id == user.db_id
    assert subscription.patreon_user_id == "p-653"
    assert subscription.access_token == "new-access"


async def test_upsert_updates_in_place_and_clears_revoke():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_654)
    existing = create_premium_subscription(
        user_id=user.db_id,
        patreon_user_id="p-old",
        access_token="old-access",
        revoked_time=dt.datetime.now(dt.UTC),
    )
    session.add_object(existing, "user_id")

    result = await upsert_subscription(session, user, link_pair(), "p-654")

    assert result is existing
    assert existing not in session.objects_added  # updated in place, not recreated
    assert existing.revoked_time is None
    assert existing.access_token == "new-access"
    assert existing.patreon_user_id == "p-654"
