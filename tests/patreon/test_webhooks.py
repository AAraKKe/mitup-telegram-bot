"""Startup registration of the Patreon membership webhook.

Registration is idempotent (matched by URI) and failure-isolated (an error is metered but never
raised). These tests drive the real ``PatreonClient`` through ``httpx.MockTransport`` and persist
through the mock DB session."""

from collections.abc import Iterator

import httpx
import pytest
from sqlmodel import select

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig
from mitup_bot.models import PatreonCreatorToken, PatreonWebhook
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon import PatreonRuntime, webhooks
from mitup_bot.patreon.client import MEMBER_WEBHOOK_TRIGGERS, PatreonClient
from mitup_bot.patreon.models import WebhookAttributes, WebhookResource
from tests.helpers import MockDbSession, create_patreon_config, create_patreon_webhook
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

DESIRED_URI = webhooks.webhook_uri("bot.example", 443)


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
        (DESIRED_URI, list(MEMBER_WEBHOOK_TRIGGERS), False),
        ("https://bot.example:443/patreon/webhook?stale", list(MEMBER_WEBHOOK_TRIGGERS), True),
        (DESIRED_URI, ["members:create"], True),
    ],
)
def test_drifted_detects_uri_or_trigger_change(uri: str, triggers: list[str], expected: bool):
    assert webhooks.drifted(webhook_resource(uri, triggers), DESIRED_URI) is expected


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
