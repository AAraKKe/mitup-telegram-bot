import contextlib
import datetime as dt
import re
from collections.abc import AsyncIterator, Iterator
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from freezegun import freeze_time
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from structlog.typing import EventDict

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig, RunModes
from mitup_bot.exceptions import PatreonApiError
from mitup_bot.models import PatreonPendingLink
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.patreon import PatreonClient, PatreonRuntime, TokenPair, oauth, pairing
from mitup_bot.patreon.models import IdentityAttributes, IdentityData, IdentityResponse
from mitup_bot.web import patreon as web_patreon
from mitup_bot.web.patreon import (
    CallbackOutcome,
    PatreonCallbackParams,
    ResolvedCallback,
    resolve_callback,
)
from tests.helpers import (
    MetricAssertions,
    build_ptb_app_mock,
    build_test_web_app,
    build_web_client,
    create_patreon_config,
    make_test_metrics_client,
)
from tests.helpers.stub_db import MockDbSession

BOT_USERNAME = "MitupTestBot"


@pytest.fixture
def staging_session(monkeypatch: pytest.MonkeyPatch) -> MockDbSession:
    """Swap ``db.begin`` for one yielding a mock session, so the callback's staging write runs
    against the stub instead of a real transaction. Returns the session so a test can read the
    ``PatreonPendingLink`` the callback added."""
    session = MockDbSession()

    @contextlib.asynccontextmanager
    async def fake_begin() -> AsyncIterator[MockDbSession]:
        yield session

    monkeypatch.setattr("mitup_bot.db.begin", fake_begin)
    return session


def staged_link(session: MockDbSession) -> PatreonPendingLink:
    """The single pending link the callback staged."""
    staged = [obj for obj in session.objects_added if isinstance(obj, PatreonPendingLink)]
    assert len(staged) == 1, f"expected exactly one staged pending link, got {len(staged)}"
    return staged[0]


def rendered_pairing_code(response_text: str) -> str:
    """The pairing code the result page rendered, read back out of the deep-link button."""
    match = re.search(rf"\?start={pairing.PAIRING_DEEP_LINK_PREFIX}_([A-Za-z0-9_-]+)", response_text)
    assert match is not None, "the result page did not render a pairing deep link"
    return match.group(1)


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

    identity = IdentityResponse(data=IdentityData(id="patreon-1", attributes=IdentityAttributes(full_name="Ada L")))
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


async def test_bare_hit_gets_generic_landing(web_app: FastAPI):
    # A bare visit (no Patreon params) gets the generic 404 landing that never mentions Patreon.
    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback")

    assert response.status_code == 404
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
        state = oauth.encode_state(patreon_config)

    # 20 minutes later: past the 15-minute TTL.
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
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c", "state": state})

    FakePatreonClient.exchange_error = None
    assert response.status_code == 502


# --- The consent leg stages a pairing code and grants nothing ---


async def test_successful_consent_stages_a_pending_link_and_renders_its_code(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    assert response.status_code == 200
    pending = staged_link(staging_session)
    assert pending.patreon_user_id == "patreon-1"
    # The display name is what lets the confirmation prompt name the account in words.
    assert pending.patreon_full_name == "Ada L"
    # The browser leg cannot know who will redeem, so it leaves the claimer for Telegram to fill in.
    assert pending.claimed_tg_user_id is None
    # The page shows the code whose hash was stored, and stores nothing that reveals it.
    assert pairing.hash_pairing_code(rendered_pairing_code(response.text)) == pending.code_hash


async def test_consent_page_offers_a_deep_link_and_a_typable_fallback(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    code = rendered_pairing_code(response.text)
    assert f'href="https://t.me/{BOT_USERNAME}?start={pairing.PAIRING_DEEP_LINK_PREFIX}_{code}"' in response.text
    # Selectable fallback for anyone whose browser will not hand the deep link to Telegram.
    assert f'<p class="code">/start {pairing.PAIRING_DEEP_LINK_PREFIX}_{code}</p>' in response.text
    assert "finish" in response.text.lower()


async def test_consent_page_never_names_a_telegram_account(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    # The browser leg learns no Telegram identity, so the page it renders cannot address one. This
    # is what makes the page safe to be looked at by whoever completed the consent.
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    assert "your own telegram account" in response.text.lower()
    assert "nothing is connected until you do it" in response.text.lower()


async def test_two_consents_stage_two_independent_codes(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)

    async with build_web_client(web_app) as client:
        first = await client.get(
            "/patreon/callback", params={"code": "c1", "state": oauth.encode_state(patreon_config)}
        )
        second = await client.get(
            "/patreon/callback", params={"code": "c2", "state": oauth.encode_state(patreon_config)}
        )

    assert rendered_pairing_code(first.text) != rendered_pairing_code(second.text)


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


def test_resolve_patreon_error_carries_error(patreon_config: PatreonConfig):
    resolved = resolve_callback(PatreonCallbackParams(error="access_denied"))
    assert resolved.outcome is CallbackOutcome.PATREON_ERROR
    assert resolved.error == "access_denied"


def test_resolve_missing_params_when_state_absent(patreon_config: PatreonConfig):
    resolved = resolve_callback(PatreonCallbackParams(code="c"))
    assert resolved.outcome is CallbackOutcome.MISSING_PARAMS


def test_resolve_state_expired_carries_age(patreon_config: PatreonConfig):
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(patreon_config)
    with freeze_time("2026-07-05 12:20:00"):
        resolved = resolve_callback(PatreonCallbackParams(code="c", state=state))
    assert resolved.outcome is CallbackOutcome.STATE_EXPIRED
    assert resolved.state_age_seconds == pytest.approx(20 * 60, abs=2)


def test_resolve_state_invalid_never_raises(patreon_config: PatreonConfig):
    # A tampered token must map to an outcome, never let PatreonStateInvalid escape as a 422.
    resolved = resolve_callback(PatreonCallbackParams(code="c", state="tampered-token"))
    assert resolved.outcome is CallbackOutcome.STATE_INVALID


def test_resolve_valid_carries_only_the_authorization_code(patreon_config: PatreonConfig):
    # A resolved callback exposes no Telegram identity because the state never carried one.
    resolved = resolve_callback(PatreonCallbackParams(code="the-code", state=oauth.encode_state(patreon_config)))
    assert resolved.outcome is CallbackOutcome.VALID
    assert resolved.code == "the-code"
    assert not any(field.startswith("tg_") or field == "message_id" for field in vars(resolved))


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
        state = oauth.encode_state(patreon_config)

    # 20 minutes later: well past the 15-minute TTL, so slow consent rather than clock skew.
    with freeze_time("2026-07-05 12:20:00"):
        with capture_logs(processors=[merge_contextvars]) as logs:
            async with build_web_client(web_app) as client:
                await client.get("/patreon/callback", params={"code": "c", "state": state})

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["stage"] == "decode_state"
    assert failure["outcome"] == "state_expired"
    assert failure["state_age_seconds"] == pytest.approx(20 * 60, abs=2)
    assert failure["state_ttl_seconds"] == 900
    assert failure["clock_skew_suspected"] is False


async def test_callback_clock_skew_is_flagged_when_age_below_ttl(web_app: FastAPI, patreon_config: PatreonConfig):
    # A token minted "ahead" of the validating clock is rejected as expired with an age under the TTL.
    with freeze_time("2026-07-05 12:10:00"):
        state = oauth.encode_state(patreon_config)

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
    state = oauth.encode_state(patreon_config)

    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback", params={"code": "c", "state": state})

    FakePatreonClient.exchange_error = None

    exchange_log = one_log(logs, "Patreon token or identity exchange failed")
    assert exchange_log["stage"] == "token_exchange"
    assert exchange_log["error_type"] == "PatreonApiError"

    failure = one_log(logs, "Patreon callback did not complete")
    assert failure["outcome"] == "patreon_api_error"


async def test_callback_logs_identity_fetch_then_staging(
    web_app: FastAPI,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config)

    with capture_logs(processors=[merge_contextvars]) as logs:
        async with build_web_client(web_app) as client:
            await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    identity_log = one_log(logs, "Patreon identity fetched")
    assert identity_log["flow"] == "patreon_oauth_callback"
    assert identity_log["stage"] == "identity_fetch"
    assert identity_log["patreon_user_id"] == "patreon-1"
    assert identity_log["is_active_member"] is False

    staged = one_log(logs, "Pending Patreon link staged")
    assert staged["stage"] == "stage_pending_link"
    assert staged["outcome"] == "pending_link_staged"
    assert staged["patreon_user_id"] == "patreon-1"
    assert staged["has_display_name"] is True
    assert staged["expires_in_seconds"] == pairing.PAIRING_CODE_TTL_SECONDS
    # No log line on this path can name a Telegram user, because the flow never learns one.
    assert all("tg_user_id" not in entry for entry in logs)


# --- Branded template rendering (the pages are filled from templates/patreon_result.html) ---


def test_render_result_page_fills_branded_template():
    response = web_patreon.render_result_page("A Title", "A message body.", "MitupBot")

    body = bytes(response.body).decode()
    assert response.status_code == 200
    assert response.media_type == "text/html"
    # Title and message are substituted into the Mitup-branded shell.
    assert "<h1>A Title</h1>" in body
    assert "A message body." in body
    # The shell carries the embedded Mitup logo (a self-contained data URI, no external image) and
    # the return link to the bot.
    assert 'class="logo"' in body
    assert 'src="data:image/png;base64,' in body
    assert 'alt="Mitup"' in body
    assert "mitup" in body.lower()
    assert '<a class="cta" href="https://t.me/MitupBot">Open Mitup</a>' in body


def test_render_result_page_omits_the_bot_link_without_a_username():
    response = web_patreon.render_result_page("T", "M", None)

    body = bytes(response.body).decode()
    assert 'class="cta"' not in body
    assert "<h1>T</h1>" in body


def test_pairing_actions_escape_the_rendered_markup():
    # The code alphabet is base64url and the username comes from Telegram, but the escaping is the
    # template's guarantee rather than an assumption about its inputs.
    actions = web_patreon.pairing_actions('bot"><script>', "a-code")

    assert "<script>" not in actions
    assert "&lt;script&gt;" in actions


def test_pairing_actions_fall_back_to_the_command_without_a_username():
    # With no bot username there is no deep link to build, but the code must still be reachable.
    actions = web_patreon.pairing_actions(None, "a-code")

    assert 'class="cta"' not in actions
    assert f'<p class="code">/start {pairing.PAIRING_DEEP_LINK_PREFIX}_a-code</p>' in actions


# --- Patreon link funnel metrics ---


async def test_callback_funnel_counts_started_only_until_telegram_confirms(
    ptb_app: MagicMock,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    # A completed consent is only half the funnel now: the browser leg counts as started, and
    # FLOW_COMPLETED is emitted by the redemption handler once the code lands in Telegram. The gap
    # between the two is what measures drop-off at the hand-off.
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)
    web_app = build_test_web_app(ptb_app=ptb_app, run_mode=RunModes.WEBHOOK, metrics_client=metrics_client)
    FakePatreonClient.exchange_error = None
    monkeypatch.setattr(web_patreon, "PatreonClient", FakePatreonClient)
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "c", "state": state})

    assert response.status_code == 200
    metrics.assert_emitted(name=MetricKey.FLOW_STARTED, value=1, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


async def test_callback_funnel_denied_consent_counts_started_only(ptb_app: MagicMock, patreon_config: PatreonConfig):
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)
    web_app = build_test_web_app(ptb_app=ptb_app, run_mode=RunModes.WEBHOOK, metrics_client=metrics_client)

    async with build_web_client(web_app) as client:
        await client.get("/patreon/callback", params={"error": "access_denied"})

    metrics.assert_emitted(name=MetricKey.FLOW_STARTED, value=1, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


async def test_callback_funnel_bare_hit_emits_nothing(ptb_app: MagicMock, patreon_config: PatreonConfig):
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)
    web_app = build_test_web_app(ptb_app=ptb_app, run_mode=RunModes.WEBHOOK, metrics_client=metrics_client)

    async with build_web_client(web_app) as client:
        await client.get("/patreon/callback")

    metrics.assert_not_emitted(name=MetricKey.FLOW_STARTED)
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


async def test_an_endpoints_patreon_round_trips_land_on_the_requests_metrics_client(
    ptb_app: MagicMock,
    patreon_config: PatreonConfig,
    monkeypatch: pytest.MonkeyPatch,
    staging_session: MockDbSession,
):
    # The web plane has no invocation wrapper of its own, so without the middleware publishing the
    # client, every Patreon call behind an endpoint writes its line and drops its timing sample.
    # Driving the real client through a mock transport is what makes the whole chain observable here.
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)
    web_app = build_test_web_app(ptb_app=ptb_app, run_mode=RunModes.WEBHOOK, metrics_client=metrics_client)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600, "token_type": "Bearer"}
            )
        return httpx.Response(200, json={"data": {"id": "patreon-1", "type": "user", "attributes": {}}})

    monkeypatch.setattr(
        web_patreon,
        "PatreonClient",
        lambda config: PatreonClient(config, transport=httpx.MockTransport(handler)),
    )
    state = oauth.encode_state(patreon_config)

    async with build_web_client(web_app) as client:
        response = await client.get("/patreon/callback", params={"code": "the-code", "state": state})

    assert response.status_code == 200
    # The code exchange and the identity read are two round-trips, each with its own sample.
    metrics.assert_emitted(name="PatreonApiFault", times=2)
    metrics.assert_emitted(name="PatreonApiFault", value=0, times=2)
