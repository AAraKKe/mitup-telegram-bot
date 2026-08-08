"""The signed ``POST /patreon/webhook`` endpoint and its membership-application logic.

Two layers, mirroring ``test_patreon.py``: HTTP-level tests pin the status-code contract (403 bad/missing
signature, 400 malformed body, 200 apply/no-op), the fault metrics and the stage log lines with the
processing mocked out;
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
from structlog.testing import capture_logs

from mitup_bot import patreon
from mitup_bot.config import PatreonConfig, RunModes
from mitup_bot.hosts_group import HostsGroupState
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
from mitup_bot.views.collaborate import hosts_group_readmitted_view
from mitup_bot.web import patreon as web_patreon
from mitup_bot.web.patreon import (
    SUPPORT_GRACE_DAYS,
    ChangeReason,
    LevelReason,
    SignatureVerdict,
    WebhookApplied,
    apply_membership_event,
    apply_membership_transition,
    signature_verdict,
    target_level,
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
    "secret, signature, verdict",
    [
        (SECRET, sign(SECRET, b"body"), SignatureVerdict.VALID),
        (SECRET, "deadbeef", SignatureVerdict.DIGEST_MISMATCH),
        (SECRET, None, SignatureVerdict.MISSING_SIGNATURE_HEADER),
        (None, sign(SECRET, b"body"), SignatureVerdict.NO_SECRET_REGISTERED),
        # Header bytes >= 0x80 arrive latin-1-decoded; rejecting them must not raise.
        (SECRET, "deadbee\xff", SignatureVerdict.DIGEST_MISMATCH),
    ],
)
def test_signature_verdict(secret: str | None, signature: str | None, verdict: SignatureVerdict):
    assert signature_verdict(secret, b"body", signature) is verdict


def test_target_level_maps_amounts_and_cancellations(patreon_config: PatreonConfig):
    assert target_level("members:update", member_resource(cents=100), patreon_config).level is SupporterLevel.HOST_1
    assert target_level("members:update", member_resource(cents=500), patreon_config).level is SupporterLevel.HOST_2
    assert target_level("members:update", member_resource(cents=1000), patreon_config).level is SupporterLevel.HOST_3
    # A delete or a non-active member is a cancellation regardless of amount, and the two are
    # different operator answers: one is a cancellation, the other a pledge that stopped clearing.
    deleted = target_level("members:delete", member_resource(cents=1000), patreon_config)
    assert (deleted.level, deleted.reason) == (SupporterLevel.NONE, LevelReason.DELETE_TRIGGER)
    lapsed = target_level("members:update", member_resource(active=False), patreon_config)
    assert (lapsed.level, lapsed.reason) == (SupporterLevel.NONE, LevelReason.NOT_ACTIVE_PATRON)


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
    with capture_logs() as logs:
        async with build_web_client(app) as client:
            response = await client.post(
                "/patreon/webhook",
                content=body,
                headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
            )

    assert response.status_code == 200
    apply_mock.assert_awaited_once()
    # Every fault series clears to its 0-baseline on the healthy apply path.
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=0)
    # What the delivery carried rides the stage line, not a series.
    received = next(entry for entry in logs if entry["event"] == "Patreon webhook received")
    assert received["stage"] == "receive"
    assert received["trigger"] == "members:update"
    assert received["signed"] is True


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

    # The fault surfaces as 500 (Patreon retries); the metric fires 1 and never its 0-baseline.
    assert response.status_code == 500
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=1)
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_FAULT, value=0)


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
    # A rejected delivery never reaches the signature-valid 0-baseline.
    metrics.assert_not_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)


async def test_non_ascii_signature_header_returns_403_and_emits_forbidden_metric(
    endpoint_app: tuple[FastAPI, MetricAssertions], monkeypatch: pytest.MonkeyPatch
):
    # A signature byte >= 0x80 reaches verify_signature latin-1-decoded, as a non-ASCII str —
    # the one input a str-level compare_digest cannot handle (TypeError). Verification runs
    # ahead of the try block that meters faults, so this has to be rejected as a plain bad
    # signature: 403 plus FORBIDDEN, never a 500 that emits nothing.
    app, metrics = endpoint_app
    monkeypatch.setattr(web_patreon.webhooks, "load_webhook_secret", AsyncMock(return_value=SECRET))
    apply_mock = AsyncMock()
    monkeypatch.setattr(web_patreon, "apply_membership_event", apply_mock)

    body = json.dumps(member_dict()).encode()
    async with build_web_client(app) as client:
        response = await client.post(
            "/patreon/webhook",
            content=body,
            headers=[(b"X-Patreon-Signature", b"\xffnot-the-signature"), (b"X-Patreon-Event", b"members:update")],
        )

    assert response.status_code == 403
    apply_mock.assert_not_awaited()
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=1)


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
    with capture_logs() as logs:
        async with build_web_client(app) as client:
            response = await client.post(
                "/patreon/webhook",
                content=body,
                headers={"X-Patreon-Signature": sign(SECRET, body), "X-Patreon-Event": "members:update"},
            )

    assert response.status_code == 400
    apply_mock.assert_not_awaited()
    # The signature verified (FORBIDDEN cleared to 0), then the body failed to parse.
    metrics.assert_emitted(name=MetricKey.PATREON_WEBHOOK_FORBIDDEN, value=0)
    malformed = next(entry for entry in logs if entry["event"] == "Malformed Patreon webhook payload")
    assert malformed["log_level"] == "warning"
    assert malformed["stage"] == "parse"


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

    with capture_logs() as logs:
        outcome = await apply_membership_event(
            api, "members:create", WebhookMemberPayload.model_validate(member_dict(cents=500))
        )

    assert outcome is WebhookApplied.UPGRADED
    assert user.supporter_level is SupporterLevel.HOST_2
    # The 500-cent pledge lands on Patron, so the DM must be the Patron unlock message specifically.
    api.assert_send_message_to_user_called(user, SupporterNotificationMessages.PATRON_UNLOCKED.get(lang=user.lang))
    # What the delivery changed rides the apply line: this is the branch where it changed something.
    applied = next(entry for entry in logs if entry["event"] == "Patreon webhook applied")
    assert applied["stage"] == "apply"
    assert applied["outcome"] == "upgraded"
    assert applied["previous_level"] == SupporterLevel.NONE.value
    assert applied["supporter_level"] == SupporterLevel.HOST_2.value


async def test_apply_downgrade_notifies(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.HOST_3)
    patch_begin_write(session)
    api = MockApi()

    # An active member now entitled to a lower tier: still a supporter, so the drop sends the neutral
    # per-tier DM naming the tier they landed on (Organizer -> Patron at 500 cents).
    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.DOWNGRADED
    assert user.supporter_level is SupporterLevel.HOST_2
    api.assert_send_message_to_user_called(user, SupporterNotificationMessages.PATRON_TIER_SET.get(lang=user.lang))


async def test_apply_delete_starts_grace_and_keeps_perks(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig
):
    session = MockDbSession()
    user, subscription = seed_link(session, level=SupporterLevel.HOST_2)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:delete", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.GRACE_STARTED
    # Perks stay on for the grace window; the daily job revokes when it elapses.
    assert user.supporter_level is SupporterLevel.HOST_2
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
    user, subscription = seed_link(session, level=SupporterLevel.HOST_2)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(active=False))
    )

    assert outcome is WebhookApplied.GRACE_STARTED
    assert user.supporter_level is SupporterLevel.HOST_2
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
    user, _subscription = seed_link(session, level=SupporterLevel.HOST_2)
    patch_begin_write(session)
    api = MockApi()

    with capture_logs() as logs:
        outcome = await apply_membership_event(
            api, "members:update", WebhookMemberPayload.model_validate(member_dict(cents=500))
        )

    assert outcome is WebhookApplied.UNCHANGED
    assert user.supporter_level is SupporterLevel.HOST_2
    api.assert_method_just_called("send_message_to_user", times=0)
    # The no-op branch of the same line, so a delivery that changed nothing stays distinguishable.
    applied = next(entry for entry in logs if entry["event"] == "Patreon webhook applied")
    assert applied["outcome"] == "unchanged"


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

    transition = apply_membership_transition(user, subscription, SupporterLevel.HOST_2)

    assert transition.applied is WebhookApplied.UPGRADED
    assert transition.reason is ChangeReason.TIER_UPGRADE
    assert subscription.support_expiration is not None
    assert subscription.expiration_notified is False


def test_apply_transition_starts_grace_on_loss_and_keeps_level():
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.HOST_2
    subscription = create_supporter_subscription(
        user_id=1, patreon_user_id="p-1", support_expiration=dt.datetime.now(dt.UTC), expiration_notified=False
    )

    transition = apply_membership_transition(user, subscription, SupporterLevel.NONE)

    assert transition.applied is WebhookApplied.GRACE_STARTED
    assert transition.reason is ChangeReason.MEMBERSHIP_LOST_GRACE_OPENED
    # The level is retained (perks stay on) and the runway is pushed out to the cancellation grace,
    # marked notified so the daily due-flow revokes rather than re-announcing grace.
    assert user.supporter_level is SupporterLevel.HOST_2
    assert_grace_window(subscription)


@pytest.mark.parametrize(
    "current, target, reason",
    [
        (SupporterLevel.HOST_2, SupporterLevel.HOST_2, ChangeReason.ALREADY_AT_TARGET_LEVEL),
        (SupporterLevel.NONE, SupporterLevel.NONE, ChangeReason.NOTHING_TO_LOSE),
    ],
)
def test_apply_transition_separates_the_two_unchanged_situations(
    current: SupporterLevel, target: SupporterLevel, reason: ChangeReason
):
    # Both report UNCHANGED, but a repeated event for a patron already on their tier and a
    # cancellation for somebody who never had one are different support answers.
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = current
    subscription = create_supporter_subscription(user_id=1, patreon_user_id="p-1")

    transition = apply_membership_transition(user, subscription, target)

    assert transition.applied is WebhookApplied.UNCHANGED
    assert transition.reason is reason


def test_apply_transition_gain_below_the_grant_floor_changes_nothing():
    # An event landing below a level fully propped up by the manual grant is a no-op, and the
    # reason names the grant so the record does not read as "already entitled to this".
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.HOST_3
    user.granted_supporter_level = SupporterLevel.HOST_3
    subscription = create_supporter_subscription(user_id=1, patreon_user_id="p-1")

    transition = apply_membership_transition(user, subscription, SupporterLevel.HOST_1)

    assert transition.applied is WebhookApplied.UNCHANGED
    assert transition.reason is ChangeReason.LEVEL_HELD_BY_GRANT
    assert user.supporter_level is SupporterLevel.HOST_3


def test_apply_transition_downgrade_clamps_to_the_grant_floor():
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.HOST_3
    user.granted_supporter_level = SupporterLevel.HOST_2
    subscription = create_supporter_subscription(user_id=1, patreon_user_id="p-1")

    transition = apply_membership_transition(user, subscription, SupporterLevel.HOST_1)

    assert transition.applied is WebhookApplied.DOWNGRADED
    assert transition.reason is ChangeReason.TIER_DOWNGRADE
    assert user.supporter_level is SupporterLevel.HOST_2


def test_apply_transition_loss_with_covering_grant_floor_opens_no_grace():
    # The granted floor covers everything the user holds, so a membership loss has nothing to take
    # away: no grace window, no runway change, no revoke to schedule.
    user = create_user(id=1, tg_user_id=1)
    user.supporter_level = SupporterLevel.HOST_2
    user.granted_supporter_level = SupporterLevel.HOST_2
    subscription = create_supporter_subscription(user_id=1, patreon_user_id="p-1")

    transition = apply_membership_transition(user, subscription, SupporterLevel.NONE)

    assert transition.applied is WebhookApplied.UNCHANGED
    assert transition.reason is ChangeReason.NOTHING_TO_LOSE
    assert user.supporter_level is SupporterLevel.HOST_2
    assert subscription.support_expiration is None


# --- Hosts-only group re-admit on reactivation ---

HOSTS_GROUP_CHAT_ID = -1001234567890
HOSTS_GROUP_INVITE_URL = "https://t.me/+hostsonly"


@pytest.fixture
def reset_hosts_group() -> Iterator[None]:
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = None
    HostsGroupState.invite_url = None
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


def sent_views(api: MockApi) -> list[object]:
    """The views passed to every send_message_to_user call, so a test can check whether the
    readmission DM was among them without counting the upgrade notification."""
    return [call.kwargs["view"] for call in api.call_args_list("send_message_to_user")]


async def test_reactivation_readmits_banned_host_with_dm(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig, reset_hosts_group: None
):
    """A NONE -> supporter reactivation of a banned host lifts the ban and sends the welcome-back
    view: the readmission copy with a Join button (the group is configured) plus a Main-menu button."""
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    HostsGroupState.invite_url = HOSTS_GROUP_INVITE_URL
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.NONE)
    patch_begin_write(session)
    api = MockApi()
    api.register_on_method("is_chat_banned", return_value=True)

    outcome = await apply_membership_event(
        api, "members:create", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.UPGRADED
    api.assert_method_just_called("unban_chat_member", times=1)
    assert api.call_args("unban_chat_member").kwargs == {
        "chat_id": HOSTS_GROUP_CHAT_ID,
        "tg_user_id": user.tg_user_id,
        "only_if_banned": True,
    }
    assert hosts_group_readmitted_view(user.lang, HOSTS_GROUP_INVITE_URL) in sent_views(api)


async def test_reactivation_unbans_never_banned_host_without_dm(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig, reset_hosts_group: None
):
    """A reactivation of a host who was never banned runs the idempotent unban but sends no DM."""
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.NONE)
    patch_begin_write(session)
    api = MockApi()
    # is_chat_banned defaults to False: the returning host was never banned.

    outcome = await apply_membership_event(
        api, "members:create", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.UPGRADED
    api.assert_method_just_called("unban_chat_member", times=1)
    assert hosts_group_readmitted_view(user.lang, HostsGroupState.invite_url) not in sent_views(api)


async def test_tier_to_tier_upgrade_does_not_readmit(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig, reset_hosts_group: None
):
    """A move between host tiers (already a supporter) leaves any group membership untouched."""
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    session = MockDbSession()
    user, _subscription = seed_link(session, level=SupporterLevel.HOST_2)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:update", WebhookMemberPayload.model_validate(member_dict(cents=1000))
    )

    assert outcome is WebhookApplied.UPGRADED
    assert user.supporter_level is SupporterLevel.HOST_3
    api.assert_method_just_called("unban_chat_member", times=0)


async def test_reactivation_noop_when_hosts_group_unconfigured(
    patch_begin_write: Callable[[MockDbSession], None], patreon_config: PatreonConfig, reset_hosts_group: None
):
    # reset_hosts_group leaves chat_id None: a reactivation grants perks but never touches the group.
    session = MockDbSession()
    seed_link(session, level=SupporterLevel.NONE)
    patch_begin_write(session)
    api = MockApi()

    outcome = await apply_membership_event(
        api, "members:create", WebhookMemberPayload.model_validate(member_dict(cents=500))
    )

    assert outcome is WebhookApplied.UPGRADED
    api.assert_method_just_called("unban_chat_member", times=0)
    api.mock_method("is_chat_banned").assert_not_called()
