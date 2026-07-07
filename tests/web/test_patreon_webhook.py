"""The signed ``POST /patreon/webhook`` endpoint and its membership-application logic.

Two layers, mirroring ``test_patreon.py``: HTTP-level tests pin the status-code contract (403 bad/missing
signature, 400 malformed body, 200 apply/no-op) and metric emission with the processing mocked out;
unit-level tests exercise ``apply_membership_event`` and the pure helpers against the mock session."""

import contextlib
import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig, RunModes
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon import PatreonRuntime
from mitup_bot.patreon.models import (
    MemberAttributes,
    MemberRelationships,
    MemberResource,
    Relationship,
    ResourceIdentifier,
    WebhookMemberPayload,
)
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages
from mitup_bot.web import patreon as web_patreon
from mitup_bot.web.patreon import (
    SUPPORT_GRACE_DAYS,
    WebhookApplied,
    apply_membership_event,
    apply_membership_transition,
    target_level,
    verify_signature,
)
from tests.helpers import (
    MockApi,
    build_ptb_app_mock,
    build_test_web_app,
    build_web_client,
    create_patreon_config,
    create_supporter_subscription,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client
from tests.helpers.stub_db import MockDbSession

SECRET = "webhook-signing-secret"


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


def member_dict(patreon_user_id: str | None = "patreon-1", *, active: bool = True, cents: int = 500) -> dict:
    relationships: dict = {}
    if patreon_user_id is not None:
        relationships["user"] = {"data": {"id": patreon_user_id, "type": "user"}}
    return {
        "data": {
            "id": "member-1",
            "type": "member",
            "attributes": {
                "patron_status": "active_patron" if active else "former_patron",
                "currently_entitled_amount_cents": cents,
            },
            "relationships": relationships,
        }
    }


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.md5).hexdigest()


def member_resource(patreon_user_id: str = "patreon-1", *, active: bool = True, cents: int = 500) -> MemberResource:
    return MemberResource(
        id="member-1",
        attributes=MemberAttributes(
            patron_status="active_patron" if active else "former_patron", currently_entitled_amount_cents=cents
        ),
        relationships=MemberRelationships(user=Relationship(data=ResourceIdentifier(id=patreon_user_id))),
    )


# --- Pure helpers ---


@pytest.mark.parametrize(
    "secret, signature, ok",
    [
        (SECRET, sign(SECRET, b"body"), True),
        (SECRET, "deadbeef", False),
        (SECRET, None, False),
        (None, sign(SECRET, b"body"), False),
    ],
)
def test_verify_signature(secret: str | None, signature: str | None, ok: bool):
    assert verify_signature(secret, b"body", signature) is ok


def test_target_level_maps_amounts_and_cancellations(patreon_config: PatreonConfig):
    assert target_level("members:update", member_resource(cents=100), patreon_config) is SupporterLevel.SUPPORTER
    assert target_level("members:update", member_resource(cents=500), patreon_config) is SupporterLevel.PATRON
    assert target_level("members:update", member_resource(cents=1000), patreon_config) is SupporterLevel.ORGANIZER
    # A delete or a non-active member is a cancellation regardless of amount.
    assert target_level("members:delete", member_resource(cents=1000), patreon_config) is SupporterLevel.NONE
    assert target_level("members:update", member_resource(active=False), patreon_config) is SupporterLevel.NONE


# --- Endpoint: signature + status-code contract (processing mocked) ---


@pytest.fixture
def endpoint_app() -> tuple[FastAPI, MetricAssertions]:
    metrics_client = make_test_metrics_client()
    ptb_app = build_ptb_app_mock()
    ptb_app.bot.username = "MitupTestBot"
    app = build_test_web_app(ptb_app=ptb_app, metrics_client=metrics_client, run_mode=RunModes.WEBHOOK)
    return app, MetricAssertions(metrics_client)


async def test_valid_signature_applies_and_returns_200(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    apply_mock = AsyncMock(return_value=WebhookApplied.UPGRADED)
    monkeypatch.setattr(web_patreon, "apply_membership_event", apply_mock)

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
        )

    assert response.status_code == 200
    apply_mock.assert_awaited_once()
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_RECEIVED)
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_APPLIED, value=1)
    # Every fault series clears to its 0-baseline on the healthy apply path.
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_MALFORMED, value=0)
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=0)


async def test_unchanged_event_emits_applied_zero(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    monkeypatch.setattr(web_patreon, "apply_membership_event", AsyncMock(return_value=WebhookApplied.UNCHANGED))

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
        )

    assert response.status_code == 200
    # A no-op emits APPLIED=0 (continuous series), never the change=1 value.
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_APPLIED, value=0)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_APPLIED, value=1)


async def test_processing_fault_returns_500_and_emits_fault(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    monkeypatch.setattr(web_patreon, "apply_membership_event", AsyncMock(side_effect=RuntimeError("boom")))

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app, raise_app_exceptions=False) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
        )

    # The fault surfaces as 500 (Patreon retries); the metric fires 1 and no 0-baseline or APPLIED.
    assert response.status_code == 500
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=1)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=0)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_APPLIED)


async def test_bad_signature_returns_403_without_processing(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    apply_mock = AsyncMock()
    monkeypatch.setattr(web_patreon, "apply_membership_event", apply_mock)

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": "not-the-signature", "X-Patreon-Event": "members:update"},
        )

    assert response.status_code == 403
    apply_mock.assert_not_awaited()
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=1)
    # A rejected delivery never reaches the signature-valid 0-baseline or the downstream series.
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_MALFORMED)


async def test_missing_signature_header_returns_403(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, _ = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    monkeypatch.setattr(web_patreon, "apply_membership_event", AsyncMock())

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post("/patreon/webhook", content=body, headers={"X-Patreon-Event": "members:update"})

    assert response.status_code == 403


async def test_no_registered_secret_returns_403(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    # No webhook registered yet: load_webhook_secret returns None, verification fails closed.
    app, _ = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=None))
    monkeypatch.setattr(web_patreon, "apply_membership_event", AsyncMock())

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
        )

    assert response.status_code == 403


async def test_malformed_body_returns_400(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    apply_mock = AsyncMock()
    monkeypatch.setattr(web_patreon, "apply_membership_event", apply_mock)

    body = b"not json at all"
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
        )

    assert response.status_code == 400
    apply_mock.assert_not_awaited()
    # The signature verified (FORBIDDEN cleared to 0), then the body failed to parse (MALFORMED=1).
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_MALFORMED, value=1)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_MALFORMED, value=0)


# --- apply_membership_event + apply_membership_transition (mock session) ---


@pytest.fixture
def patch_begin_write(monkeypatch: pytest.MonkeyPatch) -> Callable[[MockDbSession], None]:
    def patch(session: MockDbSession):
        @contextlib.asynccontextmanager
        async def fake_begin_write(api: object):
            yield session

        monkeypatch.setattr("mitup_bot.db.begin_write", fake_begin_write)

    return patch


def seed_link(
    session: MockDbSession, *, level: SupporterLevel, patreon_user_id: str = "patreon-1"
) -> tuple[User, SupporterSubscription]:
    user = create_user(id=1, tg_user_id=997_700)
    user.supporter_level = level
    subscription = create_supporter_subscription(user_id=1, patreon_user_id=patreon_user_id)
    session.add_object(subscription, "patreon_user_id")
    session.add_object(user, "id")
    return user, subscription


def assert_grace_window(subscription: SupporterSubscription):
    """A loss event opens the one-week grace: the runway is set ~SUPPORT_GRACE_DAYS out and the row is
    marked already-notified so the daily job revokes straight away when the window elapses."""
    assert subscription.support_expiration is not None
    remaining = subscription.support_expiration - dt.datetime.now(dt.UTC)
    assert (
        dt.timedelta(days=SUPPORT_GRACE_DAYS) - dt.timedelta(minutes=1)
        <= remaining
        <= dt.timedelta(days=SUPPORT_GRACE_DAYS)
    )
    assert subscription.expiration_notified is True


async def test_apply_upgrade_grants_and_notifies(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.NONE)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:create", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.UPGRADED
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_send_message_to_user_called(user, SupporterNotificationMessages.UPGRADED.get(lang=user.lang))


async def test_apply_downgrade_is_silent(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.ORGANIZER)
    patch_begin_write(session)
    api = MockApi()

    # An active member now entitled to a lower tier: still a supporter, so the drop is silent.
    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.DOWNGRADED
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_apply_delete_starts_grace_and_keeps_perks(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, subscription = seed_link(session, level=SupporterLevel.PATRON)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:delete", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.GRACE_STARTED
    # Perks stay on for the grace window; the daily job revokes when it elapses.
    assert user.supporter_level is SupporterLevel.PATRON
    assert_grace_window(subscription)
    api.assert_send_message_to_user_called(
        user, SupporterNotificationMessages.SUPPORT_ENDED_GRACE.get(lang=user.lang, days=SUPPORT_GRACE_DAYS)
    )
    # Non-tautological guard: the day count must actually interpolate into the rendered copy.
    sent = api.call_args("send_message_to_user").kwargs["view"]
    assert f"{SUPPORT_GRACE_DAYS} days" in sent.text


async def test_apply_non_active_member_starts_grace(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, subscription = seed_link(session, level=SupporterLevel.PATRON)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(active=False))
    )

    assert outcome is WebhookApplied.GRACE_STARTED
    assert user.supporter_level is SupporterLevel.PATRON
    assert_grace_window(subscription)
    api.assert_send_message_to_user_called(
        user, SupporterNotificationMessages.SUPPORT_ENDED_GRACE.get(lang=user.lang, days=SUPPORT_GRACE_DAYS)
    )


async def test_apply_loss_for_non_supporter_is_noop(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    # The user is already at NONE: a loss event has nothing to grace, so it applies nothing and is silent.
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.NONE)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:delete", WebhookMemberPayload.model_validate(member_dict(active=False))
    )

    assert outcome is WebhookApplied.UNCHANGED
    assert user.supporter_level is SupporterLevel.NONE
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_apply_unchanged_sends_nothing(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.PATRON)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.UNCHANGED
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_apply_unknown_patron_is_noop(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()  # no subscription registered for this patreon_user_id
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(api, "members:update", WebhookMemberPayload.model_validate(member_dict()))

    assert outcome is WebhookApplied.UNCHANGED
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_apply_member_without_user_relationship_is_noop(patreon_config: PatreonConfig):
    # No user relationship: patreon_user_id is None, so we return before any DB access.
    api = MockApi()
    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(patreon_user_id=None))
    )
    assert outcome is WebhookApplied.UNCHANGED
    api.assert_method_just_called("send_message_to_user", times=0)


def test_apply_transition_refreshes_grace_on_upgrade():
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.NONE
    subscription = create_supporter_subscription(user_id=1, patreon_user_id="p-1", expiration_notified=True)

    outcome = apply_membership_transition(user, subscription, SupporterLevel.PATRON)

    assert outcome is WebhookApplied.UPGRADED
    assert subscription.support_expiration is not None
    assert subscription.expiration_notified is False


def test_apply_transition_starts_grace_on_loss_and_keeps_level():
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.PATRON
    subscription = create_supporter_subscription(
        user_id=1, patreon_user_id="p-1", support_expiration=dt.datetime.now(dt.UTC), expiration_notified=False
    )

    outcome = apply_membership_transition(user, subscription, SupporterLevel.NONE)

    assert outcome is WebhookApplied.GRACE_STARTED
    # The level is retained (perks stay on) and the runway is pushed out to the cancellation grace,
    # marked notified so the daily due-flow revokes rather than re-announcing grace.
    assert user.supporter_level is SupporterLevel.PATRON
    assert_grace_window(subscription)
