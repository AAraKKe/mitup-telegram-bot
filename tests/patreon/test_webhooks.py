"""Startup registration of the Patreon membership webhook.

Registration is idempotent (matched by URI) and failure-isolated (an error is metered but never
raised). These tests drive the real ``PatreonClient`` through ``httpx.MockTransport`` and persist
through the mock DB session."""

from collections.abc import Iterator

import httpx
import pytest
import structlog
from sqlmodel import select
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig
from mitup_bot.models import PatreonCreatorToken, PatreonWebhook
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon import PatreonRuntime, webhooks
from mitup_bot.patreon.client import MEMBER_WEBHOOK_TRIGGERS, PatreonClient
from mitup_bot.patreon.models import WebhookAttributes, WebhookResource
from mitup_bot.patreon.webhooks import WebhookDrift
from tests.helpers import MockDbSession, create_patreon_config, create_patreon_webhook
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

DESIRED_URI = webhooks.webhook_uri("bot.example", 443)


@pytest.fixture(autouse=True)
def reset_registration_stage() -> Iterator[None]:
    # `register_membership_webhook` owns the reset of the `stage` bind in production; a test driving
    # `ensure_webhook` on its own leaves it set, which would ride every later line in this worker.
    yield
    structlog.contextvars.unbind_contextvars("stage")


@pytest.fixture(autouse=True)
def reset_patreon() -> Iterator[None]:
    saved = PatreonRuntime.config
    PatreonRuntime.config = None
    try:
        yield
    finally:
        PatreonRuntime.config = saved


@pytest.fixture
def patreon_config() -> PatreonConfig:
    config = create_patreon_config()
    patreon.configure(config)
    return config


def webhook_json(webhook_id: str, *, uri: str, secret: str, triggers: list[str]) -> dict:
    return {
        "type": "webhook",
        "id": webhook_id,
        "attributes": {"triggers": triggers, "uri": uri, "paused": False, "secret": secret},
    }


# --- Pure helpers ---


def test_webhook_uri_carries_member_payload_query():
    uri = webhooks.webhook_uri("bot.example", 443)
    assert (
        uri
        == "https://bot.example:443/patreon/webhook?include=user&fields[member]=patron_status,currently_entitled_amount_cents"
    )


@pytest.mark.parametrize(
    "existing, expected",
    [
        (DESIRED_URI, True),
        ("https://bot.example:443/patreon/webhook?include=user", True),  # same path, drifted query
        ("https://bot.example:443/telegram", False),
        (None, False),
    ],
)
def test_uri_matches_on_base_path(existing: str | None, expected: bool):
    assert webhooks.uri_matches(existing, DESIRED_URI) is expected


def webhook_resource(uri: str, triggers: list[str]) -> WebhookResource:
    return WebhookResource(id="wh-1", attributes=WebhookAttributes(uri=uri, triggers=triggers))


@pytest.mark.parametrize(
    "uri, triggers, expected",
    [
        (DESIRED_URI, list(MEMBER_WEBHOOK_TRIGGERS), None),
        ("https://bot.example:443/patreon/webhook?stale", list(MEMBER_WEBHOOK_TRIGGERS), WebhookDrift.URI_CHANGED),
        (DESIRED_URI, ["members:create"], WebhookDrift.TRIGGERS_CHANGED),
        (
            "https://bot.example:443/patreon/webhook?stale",
            ["members:create"],
            WebhookDrift.URI_AND_TRIGGERS_CHANGED,
        ),
    ],
)
def test_drift_reason_names_which_part_changed(uri: str, triggers: list[str], expected: WebhookDrift | None):
    assert webhooks.drift_reason(webhook_resource(uri, triggers), DESIRED_URI) is expected


# --- ensure_webhook: create / patch / idempotent ---


async def test_ensure_webhook_creates_and_persists_secret_when_absent(
    mock_session: MockDbSession, patreon_config: PatreonConfig
):
    mock_session.add_objects_with_statement(select(PatreonWebhook), ())
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            201,
            json={
                "data": webhook_json(
                    "wh-new", uri=DESIRED_URI, secret="fresh-secret", triggers=list(MEMBER_WEBHOOK_TRIGGERS)
                )
            },
        )

    async with PatreonClient(patreon_config, transport=httpx.MockTransport(handler)) as client:
        await webhooks.ensure_webhook(client, "creator-access", DESIRED_URI)

    assert "POST" in methods
    stored = next(obj for obj in mock_session.objects_added if isinstance(obj, PatreonWebhook))
    assert stored.patreon_webhook_id == "wh-new"
    assert stored.secret == "fresh-secret"
    assert stored.uri == DESIRED_URI


async def test_ensure_webhook_idempotent_when_matching(mock_session: MockDbSession, patreon_config: PatreonConfig):
    existing_row = create_patreon_webhook(patreon_webhook_id="wh-1", uri=DESIRED_URI, secret="old-secret")
    mock_session.add_objects_with_statement(select(PatreonWebhook), (existing_row,))
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json={
                "data": [
                    webhook_json(
                        "wh-1", uri=DESIRED_URI, secret="listed-secret", triggers=list(MEMBER_WEBHOOK_TRIGGERS)
                    )
                ]
            },
        )

    async with PatreonClient(patreon_config, transport=httpx.MockTransport(handler)) as client:
        await webhooks.ensure_webhook(client, "creator-access", DESIRED_URI)

    # No duplicate created, no PATCH issued — only the list GET.
    assert methods == ["GET"]
    mock_session.assert_not_added()
    # The stored row is refreshed in place with the currently-listed secret.
    assert existing_row.secret == "listed-secret"
    assert existing_row.patreon_webhook_id == "wh-1"


async def test_ensure_webhook_patches_on_drift(mock_session: MockDbSession, patreon_config: PatreonConfig):
    existing_row = create_patreon_webhook(
        patreon_webhook_id="wh-1", uri="https://bot.example:443/patreon/webhook?stale", secret="old"
    )
    mock_session.add_objects_with_statement(select(PatreonWebhook), (existing_row,))
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        webhook_json(
                            "wh-1",
                            uri="https://bot.example:443/patreon/webhook?stale",
                            secret="listed-secret",
                            triggers=list(MEMBER_WEBHOOK_TRIGGERS),
                        )
                    ]
                },
            )
        # PATCH
        return httpx.Response(
            200,
            json={
                "data": webhook_json(
                    "wh-1", uri=DESIRED_URI, secret="listed-secret", triggers=list(MEMBER_WEBHOOK_TRIGGERS)
                )
            },
        )

    async with PatreonClient(patreon_config, transport=httpx.MockTransport(handler)) as client:
        await webhooks.ensure_webhook(client, "creator-access", DESIRED_URI)

    assert "PATCH" in methods
    assert existing_row.uri == DESIRED_URI
    assert existing_row.secret == "listed-secret"


# --- register_membership_webhook: failure isolation ---


async def test_register_failure_is_isolated_and_metered(
    mock_session: MockDbSession, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)

    def handler(request: httpx.Request) -> httpx.Response:
        # The creator-token refresh fails hard, so registration cannot proceed.
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(
        webhooks, "PatreonClient", lambda config: PatreonClient(config, transport=httpx.MockTransport(handler))
    )

    # Must not raise out of startup.
    await webhooks.register_membership_webhook(DESIRED_URI, metrics_client)

    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_REGISTRATION_FAILED)


async def test_register_no_creator_token_meters_and_returns(
    mock_session: MockDbSession, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)

    def handler(request: httpx.Request) -> httpx.Response:
        # invalid_grant on the creator refresh: acquire returns None, registration cannot proceed.
        return httpx.Response(400, json={"error": "invalid_grant"})

    monkeypatch.setattr(
        webhooks, "PatreonClient", lambda config: PatreonClient(config, transport=httpx.MockTransport(handler))
    )

    await webhooks.register_membership_webhook(DESIRED_URI, metrics_client)

    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_REGISTRATION_FAILED)
    assert not any(isinstance(obj, PatreonWebhook) for obj in mock_session.objects_added)


async def test_a_paused_webhook_is_surfaced_on_the_boot_that_finds_it(
    mock_session: MockDbSession, patreon_config: PatreonConfig
):
    # Patreon pauses a webhook it cannot deliver to and tells us nothing: no event arrives, and the
    # daily reconciliation quietly becomes the only mechanism. The boot that reads these attributes
    # is the only place the fact is available.
    existing_row = create_patreon_webhook(patreon_webhook_id="wh-1", uri=DESIRED_URI, secret="old")
    mock_session.add_objects_with_statement(select(PatreonWebhook), (existing_row,))
    listed = webhook_json("wh-1", uri=DESIRED_URI, secret="s", triggers=list(MEMBER_WEBHOOK_TRIGGERS))
    listed["attributes"] |= {"paused": True, "num_consecutive_times_failed": 7}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [listed]})

    with capture_logs() as logs:
        async with PatreonClient(patreon_config, transport=httpx.MockTransport(handler)) as client:
            await webhooks.ensure_webhook(client, "creator-access", DESIRED_URI)

    (warning,) = [entry for entry in logs if entry["event"] == "Patreon webhook is paused or failing"]
    assert (warning["paused"], warning["consecutive_failures"]) == (True, 7)
    assert warning["log_level"] == "warning"
    # The matching webhook still reports as up to date; being paused is not drift.
    assert any(entry["event"] == "Patreon membership webhook already up to date" for entry in logs)


async def test_a_patch_names_which_part_drifted(mock_session: MockDbSession, patreon_config: PatreonConfig):
    stale_uri = "https://bot.example:443/patreon/webhook?stale"
    existing_row = create_patreon_webhook(patreon_webhook_id="wh-1", uri=stale_uri, secret="old")
    mock_session.add_objects_with_statement(select(PatreonWebhook), (existing_row,))

    def handler(request: httpx.Request) -> httpx.Response:
        listed = webhook_json("wh-1", uri=stale_uri, secret="s", triggers=["members:create"])
        if request.method == "GET":
            return httpx.Response(200, json={"data": [listed]})
        return httpx.Response(200, json={"data": listed})

    with capture_logs() as logs:
        async with PatreonClient(patreon_config, transport=httpx.MockTransport(handler)) as client:
            await webhooks.ensure_webhook(client, "creator-access", DESIRED_URI)

    patched = next(entry for entry in logs if entry["event"] == "Re-pointed existing Patreon membership webhook")
    assert patched["reason"] == "uri_and_triggers_changed"
    assert patched["previous_uri"] == stale_uri
    assert patched["previous_triggers"] == ["members:create"]


async def test_a_registration_failure_names_the_step_it_died_on(
    mock_session: MockDbSession, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    # Registration is failure-isolated, so the log is the only evidence it ran at all — and "Patreon
    # was unreachable" and "the PATCH was rejected" are different operator answers.
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(select(PatreonWebhook), ())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600, "token_type": "Bearer"}
            )
        if request.method == "GET":
            listed = webhook_json("wh-1", uri="https://bot.example:443/other", secret="s", triggers=[])
            listed["attributes"]["uri"] = DESIRED_URI.split("?", 1)[0]
            return httpx.Response(200, json={"data": [listed]})
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(
        webhooks, "PatreonClient", lambda config: PatreonClient(config, transport=httpx.MockTransport(handler))
    )

    with capture_logs(processors=[merge_contextvars]) as logs:
        await webhooks.register_membership_webhook(DESIRED_URI, make_test_metrics_client())

    failure = next(entry for entry in logs if entry["event"] == "Patreon webhook registration failed")
    assert failure["stage"] == "update"


async def test_a_missing_webhook_row_says_deliveries_cannot_be_verified(mock_session: MockDbSession):
    # This one condition 403s every incoming delivery under a message that otherwise reads like an
    # attack, so the cause has to be on the same stream as the rejections it produces.
    mock_session.add_objects_with_statement(select(PatreonWebhook), ())

    with capture_logs() as logs:
        assert await webhooks.load_webhook_secret() is None

    (line,) = [entry for entry in logs if entry["event"].startswith("No Patreon webhook secret stored")]
    assert line["reason"] == "no_webhook_row"


async def test_registration_publishes_a_metrics_client_for_its_outbound_calls(
    mock_session: MockDbSession, patreon_config: PatreonConfig, monkeypatch: pytest.MonkeyPatch
):
    # Startup runs outside any invocation. Without the bind the Patreon round-trips still produce
    # their lines, but nothing in the metric plane has a client to buffer their timing samples into.
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(select(PatreonWebhook), ())
    metrics_client = make_test_metrics_client()
    metrics = MetricAssertions(metrics_client)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600, "token_type": "Bearer"}
            )
        return httpx.Response(
            200,
            json={"data": [webhook_json("wh-1", uri=DESIRED_URI, secret="s", triggers=list(MEMBER_WEBHOOK_TRIGGERS))]},
        )

    monkeypatch.setattr(
        webhooks, "PatreonClient", lambda config: PatreonClient(config, transport=httpx.MockTransport(handler))
    )

    await webhooks.register_membership_webhook(DESIRED_URI, metrics_client)

    # The creator-token refresh and the webhook list are two round-trips, each with its own sample.
    metrics.assert_emitted(name="PatreonApiFault", times=2)
    metrics.assert_emitted(name="PatreonApiFault", value=0, times=2)
