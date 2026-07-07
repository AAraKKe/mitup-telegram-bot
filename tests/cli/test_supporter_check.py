import datetime as dt
import hashlib
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlmodel import select
from structlog.testing import capture_logs

from mitup_bot.cli import supporter_check
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import PatreonCreatorToken, SupporterSubscription, User
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.patreon import TokenPair
from mitup_bot.patreon.models import (
    MemberAttributes,
    MemberRelationships,
    MemberResource,
    Relationship,
    ResourceIdentifier,
)
from mitup_bot.patreon.runtime import PatreonRuntime, configure
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_patreon_config,
    create_patreon_creator_token,
    create_settings,
    create_supporter_subscription,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture(autouse=True)
def reset_runtime() -> Iterator[None]:
    saved = PatreonRuntime.config
    PatreonRuntime.config = None
    try:
        yield
    finally:
        PatreonRuntime.config = saved


@pytest.fixture(autouse=True)
def capture_structlog() -> Iterator[None]:
    """Keep the job's structlog emissions off the real pipeline: a bare emission trips the
    xdist + json-report reporter under coverage (as the rest of the CLI suite already handles)."""
    with capture_logs():
        yield


@pytest.fixture
def config() -> PatreonConfig:
    return create_patreon_config()


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.SUPPORTER_CHECK.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def active_member(patreon_user_id: str, *, active: bool = True, cents: int = 500) -> MemberResource:
    return MemberResource(
        id=f"member-{patreon_user_id}",
        attributes=MemberAttributes(
            patron_status="active_patron" if active else "former_patron",
            currently_entitled_amount_cents=cents,
        ),
        relationships=MemberRelationships(user=Relationship(data=ResourceIdentifier(id=patreon_user_id))),
    )


class FakePatreonClient:
    """Async-context Patreon client stand-in. ``refresh`` rotates a pair unless its refresh token is
    flagged revoked (raises ``PatreonTokenRevoked``)."""

    def __init__(
        self,
        *,
        members: tuple[MemberResource, ...] = (),
        revoked_refresh_tokens: frozenset[str] = frozenset(),
        new_ttl_days: int = 30,
    ):
        self.members = members
        self.revoked = revoked_refresh_tokens
        self.new_ttl_days = new_ttl_days
        self.refresh_calls: list[TokenPair] = []
        self.members_access_token: str | None = None

    async def __aenter__(self) -> FakePatreonClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def refresh(self, pair: TokenPair) -> TokenPair:
        self.refresh_calls.append(pair)
        if pair.refresh_token in self.revoked:
            raise PatreonTokenRevoked
        return TokenPair(
            access_token=f"{pair.access_token}-new",
            refresh_token=f"{pair.refresh_token}-new",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=self.new_ttl_days),
        )

    async def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]:
        self.members_access_token = access_token
        for member in self.members:
            yield member


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_seed_fingerprint_is_sha256_of_access_seed(config: PatreonConfig):
    expected = hashlib.sha256(b"creator-access-seed").hexdigest()
    assert supporter_check.seed_fingerprint(config) == expected


@pytest.mark.parametrize("aware", [True, False], ids=["aware", "naive"])
def test_days_until_handles_aware_and_naive(aware: bool):
    expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=10)
    if not aware:
        expiration = expiration.replace(tzinfo=None)
    assert supporter_check.days_until(expiration) == pytest.approx(10, abs=0.01)


async def test_active_patreon_amounts_keeps_only_active_patrons():
    client = FakePatreonClient(
        members=(
            active_member("patreon-1", cents=500),
            active_member("patreon-2", active=False),
            active_member("patreon-3", cents=0),
            active_member("patreon-4", cents=1000),
        )
    )
    assert await supporter_check.active_patreon_amounts(client, "token") == {"patreon-1": 500, "patreon-4": 1000}
    assert client.members_access_token == "token"


# ---------------------------------------------------------------------------
# Creator token: adopt / refresh / persist
# ---------------------------------------------------------------------------


async def test_creator_token_adopted_when_no_row(
    mock_session: MockDbSession, config: PatreonConfig, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    client = FakePatreonClient()

    access_token = await supporter_check.refresh_creator_token(client, config, metrics_client)

    assert access_token == "creator-access-seed-new"
    # The seed pair was the one refreshed, and a fresh row was stored with the seed fingerprint.
    assert client.refresh_calls[0].refresh_token == "creator-refresh-seed"
    stored = next(obj for obj in mock_session.objects_added if isinstance(obj, PatreonCreatorToken))
    assert stored.seed_fingerprint == supporter_check.seed_fingerprint(config)
    assert stored.access_token == "creator-access-seed-new"
    metrics.assert_emitted(
        name=supporter_check.CREATOR_TOKEN_TTL_METRIC,
        unit=MetricUnit.NONE,
        dimensions={"EventType": EventType.SUPPORTER_CHECK.value},
    )


async def test_creator_token_db_pair_wins_when_fingerprint_matches(
    mock_session: MockDbSession, config: PatreonConfig, metrics_client: MetricsClient
):
    row = create_patreon_creator_token(
        access_token="db-access",
        refresh_token="db-refresh",
        seed_fingerprint=supporter_check.seed_fingerprint(config),
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    client = FakePatreonClient()

    access_token = await supporter_check.refresh_creator_token(client, config, metrics_client)

    # The stored pair (fresher than the seed) is refreshed and updated in place; no adopt happens.
    assert client.refresh_calls[0].refresh_token == "db-refresh"
    assert access_token == "db-access-new"
    assert row.access_token == "db-access-new"
    assert row.seed_fingerprint == supporter_check.seed_fingerprint(config)
    mock_session.assert_not_added()


async def test_creator_token_reseeded_on_fingerprint_mismatch(
    mock_session: MockDbSession, config: PatreonConfig, metrics_client: MetricsClient
):
    row = create_patreon_creator_token(access_token="db-access", refresh_token="db-refresh", seed_fingerprint="stale")
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    client = FakePatreonClient()

    access_token = await supporter_check.refresh_creator_token(client, config, metrics_client)

    # A changed seed means an operator re-seed: the config pair is adopted and the fingerprint rotates.
    assert client.refresh_calls[0].refresh_token == "creator-refresh-seed"
    assert access_token == "creator-access-seed-new"
    assert row.seed_fingerprint == supporter_check.seed_fingerprint(config)


async def test_creator_token_invalid_grant_emits_fallback_ttl(
    mock_session: MockDbSession, config: PatreonConfig, metrics_client: MetricsClient
):
    row = create_patreon_creator_token(
        refresh_token="db-refresh",
        token_expiration=dt.datetime.now(dt.UTC) + dt.timedelta(days=5),
        seed_fingerprint=supporter_check.seed_fingerprint(config),
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    client = FakePatreonClient(revoked_refresh_tokens=frozenset({"db-refresh"}))

    access_token = await supporter_check.refresh_creator_token(client, config, metrics_client)

    # No auto-heal: the token is not returned and the declining stored TTL is what drives the alarm.
    assert access_token is None
    creator_ttls = [
        record for record in metrics_client.records if record.name == supporter_check.CREATOR_TOKEN_TTL_METRIC
    ]
    assert len(creator_ttls) == 1
    assert creator_ttls[0].value == pytest.approx(5, abs=0.01)


async def test_creator_token_invalid_grant_without_row_emits_zero_ttl(
    mock_session: MockDbSession, config: PatreonConfig, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    client = FakePatreonClient(revoked_refresh_tokens=frozenset({"creator-refresh-seed"}))

    access_token = await supporter_check.refresh_creator_token(client, config, metrics_client)

    assert access_token is None
    metrics.assert_emitted(name=supporter_check.CREATOR_TOKEN_TTL_METRIC, value=0.0, unit=MetricUnit.NONE)


# ---------------------------------------------------------------------------
# Step 2: due subscriptions (grace flow)
# ---------------------------------------------------------------------------


def register_due(mock_session: MockDbSession, subscription: SupporterSubscription, user: User):
    mock_session.add_objects_with_statement(
        supporter_check.DUE_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription.id), (subscription,)
    )
    mock_session.add_object(user)


def make_subscription_user(
    patreon_user_id: str = "patreon-1",
    *,
    support_expiration: dt.datetime | None = None,
    expiration_notified: bool = False,
) -> tuple[SupporterSubscription, User]:
    user = create_user(id=1, tg_user_id=101, settings=create_settings(id=1))
    subscription = create_supporter_subscription(
        user_id=1,
        patreon_user_id=patreon_user_id,
        support_expiration=support_expiration,
        expiration_notified=expiration_notified,
    )
    subscription.id = 1
    return subscription, user


async def test_due_still_member_is_extended_silently(mock_session: MockDbSession, api: MockApi):
    subscription, user = make_subscription_user(
        support_expiration=dt.datetime.now(dt.UTC) - dt.timedelta(days=1), expiration_notified=True
    )
    user.supporter_level = SupporterLevel.PATRON
    register_due(mock_session, subscription, user)

    outcome = await supporter_check.process_due_subscription(subscription.db_id, {"patreon-1": 500}, api)

    assert outcome is supporter_check.DueOutcome.EXTENDED
    assert subscription.expiration_notified is False
    assert subscription.support_expiration is not None
    assert subscription.support_expiration > dt.datetime.now(dt.UTC)
    # The lapse flow only extends grace; the level itself is reconciled by the sync pass.
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_due_lapsed_first_time_starts_grace(mock_session: MockDbSession, api: MockApi):
    subscription, user = make_subscription_user(
        support_expiration=dt.datetime.now(dt.UTC) - dt.timedelta(days=1), expiration_notified=False
    )
    user.supporter_level = SupporterLevel.PATRON
    register_due(mock_session, subscription, user)

    outcome = await supporter_check.process_due_subscription(subscription.db_id, {}, api)

    assert outcome is supporter_check.DueOutcome.GRACE_STARTED
    assert subscription.expiration_notified is True
    assert subscription.support_expiration is not None
    assert subscription.support_expiration > dt.datetime.now(dt.UTC)
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_send_message_to_user_called(
        user=user, view=SupporterNotificationMessages.GRACE_STARTED.get(lang=user.lang)
    )


async def test_due_lapsed_after_grace_revokes_to_none(mock_session: MockDbSession, api: MockApi):
    subscription, user = make_subscription_user(
        support_expiration=dt.datetime.now(dt.UTC) - dt.timedelta(days=1), expiration_notified=True
    )
    user.supporter_level = SupporterLevel.PATRON
    register_due(mock_session, subscription, user)

    outcome = await supporter_check.process_due_subscription(subscription.db_id, {}, api)

    assert outcome is supporter_check.DueOutcome.SUPPORT_LOST
    assert user.supporter_level is SupporterLevel.NONE
    api.assert_send_message_to_user_called(
        user=user, view=SupporterNotificationMessages.SUPPORT_LOST.get(lang=user.lang)
    )


async def test_due_no_longer_due_is_skipped(mock_session: MockDbSession, api: MockApi):
    subscription, _ = make_subscription_user()
    mock_session.add_objects_with_statement(
        supporter_check.DUE_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription.id), ()
    )

    outcome = await supporter_check.process_due_subscription(subscription.db_id, {}, api)

    assert outcome is supporter_check.DueOutcome.SKIPPED
    api.assert_method_just_called("send_message_to_user", times=0)


# ---------------------------------------------------------------------------
# Step 3: level sync (upgrades and between-tier downgrades)
# ---------------------------------------------------------------------------


def register_syncable(mock_session: MockDbSession, subscription: SupporterSubscription, user: User):
    mock_session.add_objects_with_statement(
        supporter_check.LIVE_LINKED_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription.id), (subscription,)
    )
    mock_session.add_object(user)


async def test_sync_promotes_none_user_to_entitled_tier(
    mock_session: MockDbSession, api: MockApi, config: PatreonConfig
):
    subscription, user = make_subscription_user()  # user starts at SupporterLevel.NONE
    register_syncable(mock_session, subscription, user)

    outcome = await supporter_check.sync_subscription_level(subscription.db_id, {"patreon-1": 500}, config, api)

    assert outcome is supporter_check.LevelSyncOutcome.UPGRADED
    assert user.supporter_level is SupporterLevel.PATRON
    assert subscription.support_expiration is not None
    assert subscription.support_expiration > dt.datetime.now(dt.UTC)
    # A none->patron promotion must announce the Patron tier specifically.
    api.assert_send_message_to_user_called(
        user=user, view=SupporterNotificationMessages.PATRON_UNLOCKED.get(lang=user.lang)
    )


async def test_sync_promotes_patron_to_organizer_and_notifies(
    mock_session: MockDbSession, api: MockApi, config: PatreonConfig
):
    subscription, user = make_subscription_user()
    user.supporter_level = SupporterLevel.PATRON
    register_syncable(mock_session, subscription, user)

    outcome = await supporter_check.sync_subscription_level(subscription.db_id, {"patreon-1": 1000}, config, api)

    assert outcome is supporter_check.LevelSyncOutcome.UPGRADED
    assert user.supporter_level is SupporterLevel.ORGANIZER
    # A patron->organizer promotion must announce the Organizer tier specifically.
    api.assert_send_message_to_user_called(
        user=user, view=SupporterNotificationMessages.ORGANIZER_UNLOCKED.get(lang=user.lang)
    )


async def test_sync_downgrades_between_tiers_notifies(mock_session: MockDbSession, api: MockApi, config: PatreonConfig):
    subscription, user = make_subscription_user()
    user.supporter_level = SupporterLevel.ORGANIZER
    register_syncable(mock_session, subscription, user)

    # Still an active member, but their entitled amount now only reaches the Patron threshold: the drop
    # sends the neutral per-tier DM naming the tier they settled on (Patron at 500 cents).
    outcome = await supporter_check.sync_subscription_level(subscription.db_id, {"patreon-1": 500}, config, api)

    assert outcome is supporter_check.LevelSyncOutcome.DOWNGRADED
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_send_message_to_user_called(
        user=user, view=SupporterNotificationMessages.PATRON_TIER_SET.get(lang=user.lang)
    )


async def test_sync_unchanged_when_level_matches(mock_session: MockDbSession, api: MockApi, config: PatreonConfig):
    subscription, user = make_subscription_user()
    user.supporter_level = SupporterLevel.PATRON
    register_syncable(mock_session, subscription, user)

    outcome = await supporter_check.sync_subscription_level(subscription.db_id, {"patreon-1": 500}, config, api)

    assert outcome is supporter_check.LevelSyncOutcome.UNCHANGED
    assert user.supporter_level is SupporterLevel.PATRON
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_sync_skips_when_not_active_member(mock_session: MockDbSession, api: MockApi, config: PatreonConfig):
    subscription, user = make_subscription_user()
    register_syncable(mock_session, subscription, user)

    # Absent from the amounts map: lapsing, so the sync leaves it to the grace flow.
    outcome = await supporter_check.sync_subscription_level(subscription.db_id, {}, config, api)

    assert outcome is supporter_check.LevelSyncOutcome.SKIPPED
    assert user.supporter_level is SupporterLevel.NONE
    api.assert_method_just_called("send_message_to_user", times=0)


# ---------------------------------------------------------------------------
# process_all: failure isolation
# ---------------------------------------------------------------------------


async def test_process_all_counts_successes():
    handled: list[int] = []

    async def handler(subscription_id: int):
        handled.append(subscription_id)

    failures: list[str] = []
    results = await supporter_check.process_all(handler, [1, 2, 3], failures)

    assert len(results) == 3
    assert handled == [1, 2, 3]
    assert failures == []


async def test_process_all_isolates_a_failing_subscription():
    async def handler(subscription_id: int):
        if subscription_id == 2:
            raise RuntimeError("boom")

    failures: list[str] = []
    results = await supporter_check.process_all(handler, [1, 2, 3], failures)

    # One row's failure is recorded but the sweep still processes the rest.
    assert len(results) == 2
    assert len(failures) == 1
    assert "subscription 2" in failures[0]


# ---------------------------------------------------------------------------
# run(): orchestration
# ---------------------------------------------------------------------------


async def test_run_noops_when_unconfigured(
    mock_session: MockDbSession, api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_construct(*args: object, **kwargs: object):
        raise AssertionError("PatreonClient must not be constructed when unconfigured")

    monkeypatch.setattr(supporter_check, "PatreonClient", fail_construct)

    await supporter_check.run(api, metrics_client)

    assert metrics_client.records == []


async def test_run_happy_path_emits_creator_ttl_and_counters(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    subscription, user = make_subscription_user(patreon_user_id="patreon-1")
    # Already at the entitled tier, so the level sync is a no-op and this run only exercises the counters.
    user.supporter_level = SupporterLevel.PATRON
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(supporter_check.DUE_SUBSCRIPTIONS, ())
    mock_session.add_objects_with_statement(supporter_check.LIVE_LINKED_SUBSCRIPTIONS, (subscription,))
    register_syncable(mock_session, subscription, user)

    client = FakePatreonClient(members=(active_member("patreon-1"),))
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    await supporter_check.run(api, metrics_client)

    # The freshly rotated creator token drives the member sweep.
    assert client.members_access_token == "creator-access-seed-new"
    metrics.assert_emitted(name=supporter_check.CREATOR_TOKEN_TTL_METRIC, unit=MetricUnit.NONE)
    # Outcome counters: everything a continuous zero on this no-op run.
    metrics.assert_emitted(name=supporter_check.CREATOR_REFRESH_FAULTS_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.UPGRADES_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.DOWNGRADES_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.GRACE_EXTENDED_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.GRACE_STARTED_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.SUPPORT_LOST_METRIC, value=0, unit=MetricUnit.COUNT)
    # Per-subscription faults: a clean run still emits the 0-baseline so the series stays continuous.
    metrics.assert_emitted(name=supporter_check.SUBSCRIPTION_FAULTS_METRIC, value=0, unit=MetricUnit.COUNT)


async def test_run_logs_summary_on_success(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    subscription, user = make_subscription_user(patreon_user_id="patreon-1")
    # Already at the entitled tier, so the level sync is a no-op and this run just logs its summary.
    user.supporter_level = SupporterLevel.PATRON
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(supporter_check.DUE_SUBSCRIPTIONS, ())
    mock_session.add_objects_with_statement(supporter_check.LIVE_LINKED_SUBSCRIPTIONS, (subscription,))
    register_syncable(mock_session, subscription, user)

    client = FakePatreonClient(members=(active_member("patreon-1"),))
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    with capture_logs() as logs:
        await supporter_check.run(api, metrics_client)

    summary = next(entry for entry in logs if entry["event"] == "supporter check complete")
    assert summary["due_processed"] == 0
    assert summary["extended"] == 0
    assert summary["grace_started"] == 0
    assert summary["support_lost"] == 0
    assert summary["upgraded"] == 0
    assert summary["downgraded"] == 0
    assert summary["active_patrons"] == 1
    assert summary["subscription_faults"] == 0


async def test_run_stops_after_creator_invalid_grant(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    row = create_patreon_creator_token(
        refresh_token="db-refresh", seed_fingerprint=supporter_check.seed_fingerprint(config)
    )
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), (row,))
    client = FakePatreonClient(revoked_refresh_tokens=frozenset({"db-refresh"}))
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    await supporter_check.run(api, metrics_client)

    # No member fetch: the run bailed out on the unrecoverable creator token.
    assert client.members_access_token is None
    # The creator-fault counter is this branch's only CloudWatch trace (it returns without raising).
    metrics.assert_emitted(name=supporter_check.CREATOR_REFRESH_FAULTS_METRIC, value=1, unit=MetricUnit.COUNT)
    # Downstream outcome counters never ran, so they are absent (not zeroed) this run.
    emitted_names = {record.name for record in metrics_client.records}
    assert supporter_check.GRACE_STARTED_METRIC not in emitted_names


async def test_run_raises_when_a_subscription_fails(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    subscription, user = make_subscription_user()
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(supporter_check.DUE_SUBSCRIPTIONS, (subscription,))
    mock_session.add_objects_with_statement(supporter_check.LIVE_LINKED_SUBSCRIPTIONS, ())

    client = FakePatreonClient(members=(active_member("patreon-1"),))
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    async def boom(subscription_id: int, active_amounts: dict[str, int], api: MockApi) -> supporter_check.DueOutcome:
        raise RuntimeError("boom")

    monkeypatch.setattr(supporter_check, "process_due_subscription", boom)

    with pytest.raises(RuntimeError, match="Supporter check failed for 1 subscriptions"):
        await supporter_check.run(api, metrics_client)

    # The lifecycle counters are emitted before the raise, so the series stay continuous on a failing run.
    metrics.assert_emitted(name=supporter_check.GRACE_STARTED_METRIC, value=0, unit=MetricUnit.COUNT)
    # The per-subscription fault count is emitted before the raise: N failures land as a datapoint.
    metrics.assert_emitted(name=supporter_check.SUBSCRIPTION_FAULTS_METRIC, value=1, unit=MetricUnit.COUNT)


async def test_run_counts_lifecycle_transitions(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    # A due, lapsed-but-unnotified row starts its grace; a separate linked non-patron gets upgraded;
    # a third active organizer drops to the patron tier (a silent between-tier downgrade).
    due_user = create_user(id=1, tg_user_id=101, settings=create_settings(id=1))
    due_user.supporter_level = SupporterLevel.PATRON
    due_sub = create_supporter_subscription(
        user_id=1, patreon_user_id="patreon-lapsed", support_expiration=dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    )
    due_sub.id = 1
    upgrade_user = create_user(id=2, tg_user_id=102, settings=create_settings(id=2))
    upgrade_sub = create_supporter_subscription(user_id=2, patreon_user_id="patreon-new")
    upgrade_sub.id = 2
    downgrade_user = create_user(
        id=3, tg_user_id=103, settings=create_settings(id=3), supporter_level=SupporterLevel.ORGANIZER
    )
    downgrade_sub = create_supporter_subscription(user_id=3, patreon_user_id="patreon-drop")
    downgrade_sub.id = 3

    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(supporter_check.DUE_SUBSCRIPTIONS, (due_sub,))
    mock_session.add_objects_with_statement(
        supporter_check.DUE_SUBSCRIPTIONS.where(SupporterSubscription.id == 1), (due_sub,)
    )
    # The two live members feed the level sync from one nomination.
    mock_session.add_objects_with_statement(supporter_check.LIVE_LINKED_SUBSCRIPTIONS, (upgrade_sub, downgrade_sub))
    register_syncable(mock_session, upgrade_sub, upgrade_user)
    register_syncable(mock_session, downgrade_sub, downgrade_user)
    mock_session.add_object(due_user)

    client = FakePatreonClient(
        members=(active_member("patreon-new", cents=500), active_member("patreon-drop", cents=500))
    )
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    await supporter_check.run(api, metrics_client)

    metrics.assert_emitted(name=supporter_check.GRACE_STARTED_METRIC, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.UPGRADES_METRIC, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.DOWNGRADES_METRIC, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.SUPPORT_LOST_METRIC, value=0, unit=MetricUnit.COUNT)
    # No still-active due member this run, so grace-extensions stay at the 0-baseline.
    metrics.assert_emitted(name=supporter_check.GRACE_EXTENDED_METRIC, value=0, unit=MetricUnit.COUNT)


async def test_run_counts_grace_extensions(
    mock_session: MockDbSession,
    api: MockApi,
    config: PatreonConfig,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    configure(config)
    # A due subscription whose patron is still active this run: the grace flow extends it (EXTENDED).
    subscription, user = make_subscription_user(
        patreon_user_id="patreon-1", support_expiration=dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    )
    user.supporter_level = SupporterLevel.PATRON
    mock_session.add_objects_with_statement(select(PatreonCreatorToken), ())
    mock_session.add_objects_with_statement(supporter_check.DUE_SUBSCRIPTIONS, (subscription,))
    mock_session.add_objects_with_statement(supporter_check.LIVE_LINKED_SUBSCRIPTIONS, ())
    register_due(mock_session, subscription, user)

    client = FakePatreonClient(members=(active_member("patreon-1", cents=500),))
    monkeypatch.setattr(supporter_check, "PatreonClient", lambda _config: client)

    await supporter_check.run(api, metrics_client)

    metrics.assert_emitted(name=supporter_check.GRACE_EXTENDED_METRIC, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=supporter_check.SUBSCRIPTION_FAULTS_METRIC, value=0, unit=MetricUnit.COUNT)
