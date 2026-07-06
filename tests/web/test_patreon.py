import contextlib
import datetime as dt
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from freezegun import freeze_time
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from structlog.typing import EventDict

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig, RunModes
from mitup_bot.exceptions import PatreonApiError
from mitup_bot.models import PremiumSubscription
from mitup_bot.patreon import PatreonRuntime, TokenPair, oauth
from mitup_bot.patreon.models import IdentityData, IdentityResponse
from mitup_bot.supporter import SupporterLevel
from mitup_bot.web import patreon as web_patreon
from mitup_bot.web.patreon import (
    CallbackOutcome,
    LinkOutcome,
    PatreonCallbackParams,
    ResolvedCallback,
    link_patreon_account,
    resolve_callback,
    upsert_subscription,
)
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


def one_log(logs: list[EventDict], event: str) -> EventDict:
    """Return the single captured structlog entry whose event string matches ``event``."""
    matching = [entry for entry in logs if entry["event"] == event]
    assert len(matching) == 1, f"expected exactly one {event!r} log, got {len(matching)}"
    return matching[0]


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


async def test_callback_partial_params_reassures_and_names_button(web_app: FastAPI, patreon_config: PatreonConfig):
    # A partial Patreon hit (code present, state missing) still gets the missing_params page — only a
    # fully bare hit falls through to the generic Mitup landing.
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c"})

    assert response.status_code == 400
    assert "incomplete" in response.text.lower()
    assert "you haven't done anything wrong" in response.text.lower()
    assert "Link Patreon account in the Collaborate menu" in response.text


async def test_bare_hit_renders_generic_mitup_404_without_mentioning_patreon(
    web_app: FastAPI, patreon_config: PatreonConfig
):
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback")

    assert response.status_code == 404
    assert "patreon" not in response.text.lower()
    assert "mitup" in response.text.lower()
    assert f"https://t.me/{BOT_USERNAME}" in response.text


async def test_bare_hit_is_logged_with_bare_landing_outcome(web_app: FastAPI, patreon_config: PatreonConfig):
    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback")

    bare = one_log(logs, "Patreon callback bare hit")
    assert bare["flow"] == "patreon_oauth_callback"
    assert bare["stage"] == "entry"
    assert bare["outcome"] == "bare_landing"
    assert bare["has_code"] is False
    assert bare["has_state"] is False
    assert bare["has_error"] is False


async def test_bare_hit_first_even_when_patreon_unconfigured(web_app: FastAPI):
    # No patreon_config fixture: Patreon is switched off. A bare visit must not reveal that — it still
    # gets the generic 404 landing, not the unconfigured page.
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback")

    assert response.status_code == 404
    assert "isn't switched on yet" not in response.text.lower()
    assert "patreon" not in response.text.lower()


async def test_unknown_query_params_still_hit_bare_landing_without_422(web_app: FastAPI, patreon_config: PatreonConfig):
    # extra="ignore" must survive end-to-end through FastAPI: junk query params with no Patreon fields
    # classify as a bare hit (404), never a 422 validation error.
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"foo": "bar", "utm_source": "scan"})

    assert response.status_code == 404
    assert "patreon" not in response.text.lower()


async def test_callback_expired_state_prompts_retry(web_app: FastAPI, patreon_config: PatreonConfig):
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(patreon_config, 997_620)

    # 80 minutes later: past the 1h TTL.
    with freeze_time("2026-07-05 13:20:00"):
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


# --- Input classification: PatreonCallbackParams + resolve_callback ---


@pytest.mark.parametrize(
    "params, has_code, has_state, has_error, is_redirect, has_required",
    [
        (PatreonCallbackParams(), False, False, False, False, False),
        (PatreonCallbackParams(code="c"), True, False, False, True, False),
        (PatreonCallbackParams(state="s"), False, True, False, True, False),
        (PatreonCallbackParams(error="e"), False, False, True, True, False),
        (PatreonCallbackParams(code="c", state="s"), True, True, False, True, True),
        (PatreonCallbackParams(code="c", state="s", error="e"), True, True, True, True, True),
    ],
)
def test_callback_params_flags(
    params: PatreonCallbackParams,
    has_code: bool,
    has_state: bool,
    has_error: bool,
    is_redirect: bool,
    has_required: bool,
):
    assert params.has_code is has_code
    assert params.has_state is has_state
    assert params.has_error is has_error
    assert params.looks_like_patreon_redirect is is_redirect
    assert params.has_required_params is has_required


def test_callback_params_ignores_unknown_fields():
    params = PatreonCallbackParams.model_validate({"foo": "bar", "code": "c"})
    assert params.code == "c"
    assert not hasattr(params, "foo")


def test_resolve_bare_hit_when_no_params():
    assert resolve_callback(PatreonCallbackParams()).outcome is CallbackOutcome.BARE


def test_resolve_unconfigured_when_patreon_off():
    # No patreon_config fixture: Patreon is switched off, but the params look like a redirect.
    resolved = resolve_callback(PatreonCallbackParams(code="c", state="s"))
    assert resolved.outcome is CallbackOutcome.UNCONFIGURED


def test_resolve_patreon_error_carries_error(patreon_config: PatreonConfig):
    resolved = resolve_callback(PatreonCallbackParams(error="access_denied"))
    assert resolved.outcome is CallbackOutcome.PATREON_ERROR
    assert resolved.error == "access_denied"


def test_resolve_missing_params_when_state_absent(patreon_config: PatreonConfig):
    resolved = resolve_callback(PatreonCallbackParams(code="c"))
    assert resolved.outcome is CallbackOutcome.MISSING_PARAMS


def test_resolve_state_expired_carries_age(patreon_config: PatreonConfig):
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(patreon_config, 997_670)
    with freeze_time("2026-07-05 13:20:00"):
        resolved = resolve_callback(PatreonCallbackParams(code="c", state=state))
    assert resolved.outcome is CallbackOutcome.STATE_EXPIRED
    assert resolved.state_age_seconds == pytest.approx(80 * 60, abs=2)


def test_resolve_state_invalid_never_raises(patreon_config: PatreonConfig):
    # A tampered token must map to an outcome, never let PatreonStateInvalid escape as a 422.
    resolved = resolve_callback(PatreonCallbackParams(code="c", state="tampered-token"))
    assert resolved.outcome is CallbackOutcome.STATE_INVALID


def test_resolve_valid_carries_tg_user_id_and_credentials(patreon_config: PatreonConfig):
    state = oauth.encode_state(patreon_config, 997_671)
    resolved = resolve_callback(PatreonCallbackParams(code="the-code", state=state))
    assert resolved.outcome is CallbackOutcome.VALID
    assert resolved.tg_user_id == 997_671
    assert resolved.code == "the-code"
    assert resolved.state == state


def test_render_terminal_page_rejects_valid_outcome():
    # VALID is the side-effecting path handled by render_resolved_callback; it must never reach the
    # terminal-page renderer, which is a pure page-picker for the non-VALID outcomes.
    with pytest.raises(AssertionError):
        web_patreon.render_terminal_page(
            PatreonCallbackParams(code="c", state="s"), ResolvedCallback(CallbackOutcome.VALID), BOT_USERNAME
        )


# --- Structured logging: every line carries flow + request_id + stage, terminal lines carry outcome ---


async def test_callback_entry_and_missing_params_are_logged(web_app: FastAPI, patreon_config: PatreonConfig):
    # Partial hit (code without state): entry line records the booleans, terminal line is missing_params.
    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback", params={"code": "c"})

    entry = one_log(logs, "Patreon callback received")
    assert entry["flow"] == "patreon_oauth_callback"
    assert isinstance(entry["request_id"], str) and entry["request_id"]
    assert entry["stage"] == "entry"
    assert entry["has_code"] is True
    assert entry["has_state"] is False
    assert entry["has_error"] is False

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["flow"] == "patreon_oauth_callback"
    assert failure["request_id"] == entry["request_id"]
    assert failure["stage"] == "entry"
    assert failure["outcome"] == "missing_params"
    assert failure["reason"] == "missing_params"
    assert failure["has_code"] is True
    assert failure["has_state"] is False


async def test_callback_state_expired_logs_age_ttl_and_no_skew(web_app: FastAPI, patreon_config: PatreonConfig):
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(patreon_config, 997_620)

    # 80 minutes later: well past the 1h TTL, so slow-consent rather than clock skew.
    with freeze_time("2026-07-05 13:20:00"):
        with capture_logs(processors=[merge_contextvars]) as logs:
            async with build_web_client(web_app) as client:
                await client.get("/patreon/callback", params={"code": "c", "state": state})

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["stage"] == "decode_state"
    assert failure["outcome"] == "state_expired"
    assert failure["state_age_seconds"] == pytest.approx(80 * 60, abs=2)
    assert failure["state_ttl_seconds"] == 3600
    assert failure["clock_skew_suspected"] is False


async def test_callback_clock_skew_is_flagged_when_age_below_ttl(web_app: FastAPI, patreon_config: PatreonConfig):
    # A token minted "ahead" of the validating clock is rejected as expired with an age under the TTL.
    with freeze_time("2026-07-05 12:10:00"):
        state = oauth.encode_state(patreon_config, 997_621)

    with freeze_time("2026-07-05 12:00:00"):
        with capture_logs(processors=[merge_contextvars]) as logs:
            async with build_web_client(web_app) as client:
                await client.get("/patreon/callback", params={"code": "c", "state": state})

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["outcome"] == "state_expired"
    assert failure["clock_skew_suspected"] is True


async def test_callback_token_exchange_failure_logs_stage_and_error_type(
    web_app: FastAPI, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    FakePatreonClient.exchange_error = PatreonApiError("boom")
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config, 997_622)

    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback", params={"code": "c", "state": state})

    FakePatreonClient.exchange_error = None

    exchange_log = one_log(logs, "Patreon token or identity exchange failed")
    assert exchange_log["stage"] == "token_exchange"
    assert exchange_log["error_type"] == "PatreonApiError"
    assert exchange_log["tg_user_id"] == 997_622

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["outcome"] == "patreon_api_error"
    assert failure["tg_user_id"] == 997_622


async def test_callback_logs_identity_fetch_before_persist(
    web_app: FastAPI, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    monkeypatch.setattr(web_patreon, "link_patreon_account", AsyncMock(return_value=LinkOutcome.LINKED_NO_PATRON))
    state = oauth.encode_state(patreon_config, 997_623)

    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    identity_log = one_log(logs, "Patreon identity fetched")
    assert identity_log["flow"] == "patreon_oauth_callback"
    assert identity_log["stage"] == "identity_fetch"
    assert identity_log["patreon_user_id"] == "patreon-1"
    assert identity_log["is_active_member"] is False
    assert identity_log["tg_user_id"] == 997_623


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
    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            api, 997_650, patreon_user_id="p-650", supporter_level=SupporterLevel.PATRON
        )

    assert outcome is LinkOutcome.LINKED_PREMIUM
    assert user.supporter_level is SupporterLevel.PATRON
    added = [obj for obj in session.objects_added if isinstance(obj, PremiumSubscription)]
    assert len(added) == 1
    assert added[0].patreon_user_id == "p-650"
    assert added[0].premium_expiration is not None
    api.assert_method_just_called("send_message_to_user", times=1)

    linked = one_log(logs, "Patreon account linked")
    assert linked["flow"] == "patreon_oauth_callback"
    assert linked["stage"] == "persist"
    assert linked["outcome"] == "linked_premium"
    assert linked["tg_user_id"] == 997_650
    assert linked["patreon_user_id"] == "p-650"
    assert linked["supporter_level"] == "patron"


async def test_link_new_non_patron_stores_without_premium(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_651)
    session.add_object(user, "tg_user_id")
    patch_begin_write(session)

    api = MockApi()
    outcome = await link_patreon_account(api, 997_651, patreon_user_id="p-651", supporter_level=SupporterLevel.NONE)

    assert outcome is LinkOutcome.LINKED_NO_PATRON
    assert user.supporter_level is SupporterLevel.NONE
    added = [obj for obj in session.objects_added if isinstance(obj, PremiumSubscription)]
    assert len(added) == 1
    assert added[0].premium_expiration is None
    api.assert_method_just_called("send_message_to_user", times=1)


async def test_link_unknown_user_returns_unknown(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()  # the user is intentionally not registered
    patch_begin_write(session)

    api = MockApi()
    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            api, 997_659, patreon_user_id="p-659", supporter_level=SupporterLevel.PATRON
        )

    assert outcome is LinkOutcome.UNKNOWN_USER
    assert not session.objects_added
    api.assert_method_just_called("send_message_to_user", times=0)

    warning = one_log(logs, "Patreon callback for an unknown Telegram user")
    assert warning["flow"] == "patreon_oauth_callback"
    assert warning["stage"] == "persist"
    assert warning["outcome"] == "unknown_user"


async def test_link_rejected_when_account_claimed_elsewhere(patch_begin_write: Callable[[MockDbSession], None]):
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_652)
    session.add_object(user, "tg_user_id")
    # A subscription for the same Patreon account already belongs to a different user.
    other = create_premium_subscription(user_id=2, patreon_user_id="p-shared")
    session.add_object(other, "patreon_user_id")
    patch_begin_write(session)

    api = MockApi()
    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            api, 997_652, patreon_user_id="p-shared", supporter_level=SupporterLevel.PATRON
        )

    assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
    assert user.supporter_level is SupporterLevel.NONE
    assert not any(isinstance(obj, PremiumSubscription) for obj in session.objects_added)
    api.assert_method_just_called("send_message_to_user", times=0)

    warning = one_log(logs, "Patreon account already linked to another Telegram user")
    assert warning["flow"] == "patreon_oauth_callback"
    assert warning["stage"] == "persist"
    assert warning["outcome"] == "already_linked_elsewhere"


async def test_upsert_creates_subscription_when_absent():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_653)

    subscription = await upsert_subscription(session, user, "p-653")

    assert subscription in session.objects_added
    assert subscription.user_id == user.db_id
    assert subscription.patreon_user_id == "p-653"


async def test_upsert_updates_in_place_and_clears_revoke():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_654)
    existing = create_premium_subscription(
        user_id=user.db_id,
        patreon_user_id="p-old",
        revoked_time=dt.datetime.now(dt.UTC),
    )
    session.add_object(existing, "user_id")

    result = await upsert_subscription(session, user, "p-654")

    assert result is existing
    assert existing not in session.objects_added  # updated in place, not recreated
    assert existing.revoked_time is None
    assert existing.patreon_user_id == "p-654"
