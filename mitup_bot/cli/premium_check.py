"""Daily premium-membership validation against Patreon.

Plugs into the recurrent-events framework as the ``PREMIUM_CHECK`` job. Each run keeps both token
families fresh (the single creator token and every linked user token), reconciles premium status
against the campaign's active-patron set, and drives the grace/upgrade/revoke transitions. The two
token-TTL metrics feed the infra alarms in #159, so their names are pinned as literal strings rather
than routed through ``MetricKey`` (whose CamelCase folding would lowercase the ``TTL`` acronym).
"""

import datetime as dt
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

import structlog
from sqlmodel import and_, col, false, func, null, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db, patreon
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import PatreonCreatorToken, PremiumSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.patreon import PatreonClient, TokenPair
from mitup_bot.patreon.models import MemberResource
from mitup_bot.utils.messages import PremiumNotificationMessages

log = structlog.get_logger(__name__)


class TokenRefresher(Protocol):
    """The one Patreon capability the token helpers depend on — narrowed so tests can drive them
    with a stub. :class:`~mitup_bot.patreon.PatreonClient` satisfies it structurally."""

    async def refresh(self, pair: TokenPair) -> TokenPair: ...


class CampaignMemberReader(Protocol):
    """The campaign-listing capability the member sweep depends on."""

    def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]: ...


# Pinned metric names for the #159 alarms. Kept as literals because the CamelCaseStrEnum used by
# MetricKey would emit "PatreonCreatorTokenTtl", breaking the alarm's exact-name match.
CREATOR_TOKEN_TTL_METRIC = "PatreonCreatorTokenTTL"
USER_TOKEN_TTL_METRIC = "PatreonUserTokenTTL"

# Per-run outcome counters (same literal-name rationale as the TTL metrics above). Dashboard/diagnosis
# material, not a paging surface: they ride the run's MetricsClient with its EventType=PremiumCheck base
# dimension only (one series per name, no per-user dimensions). Emitted every run — zeros included — so
# the series stay continuous.
USER_TOKENS_REFRESHED_METRIC = "PatreonUserTokensRefreshed"
USER_TOKEN_REFRESH_FAULTS_METRIC = "PatreonUserTokenRefreshFaults"
USER_TOKENS_REVOKED_METRIC = "PatreonUserTokensRevoked"
CREATOR_REFRESH_FAULTS_METRIC = "PatreonCreatorRefreshFaults"
UPGRADES_METRIC = "PatreonUpgrades"
GRACE_STARTED_METRIC = "PatreonGraceStarted"
PREMIUM_LOST_METRIC = "PatreonPremiumLost"


class DueOutcome(Enum):
    """What a due subscription's grace-flow pass did, so ``run`` can aggregate lifecycle counts."""

    EXTENDED = auto()
    GRACE_STARTED = auto()
    PREMIUM_LOST = auto()
    SKIPPED = auto()


class UserTokenOutcome(Enum):
    """The non-refreshed results of a user-token pass (a successful refresh returns its id/expiry pair)."""

    REVOKED = auto()
    GONE = auto()


# Supporters keep their perks for a week past each unconfirmed checkpoint, so a lapsed or
# disconnected patron gets ~two weeks of leeway (grace start, then the grace expiry) before revocation.
GRACE_PERIOD = dt.timedelta(days=7)

# Nomination sweeps: read-only, ids only. Each nominated subscription is re-loaded and re-checked
# inside its own write lifecycle, so nothing read here feeds a mutation directly.
DUE_SUBSCRIPTIONS: SelectOfScalar[PremiumSubscription] = (
    select(PremiumSubscription)
    .join(User, col(PremiumSubscription.user_id) == col(User.id))
    .where(
        and_(
            User.status == UserStatus.MEMBER,
            PremiumSubscription.premium_expiration != null(),
            PremiumSubscription.premium_expiration <= func.now(),
        )
    )
)
# Linked users without premium whose row is still live: candidates for auto-upgrade when their
# Patreon id turns up in the active-patron set.
UPGRADABLE_SUBSCRIPTIONS: SelectOfScalar[PremiumSubscription] = (
    select(PremiumSubscription)
    .join(User, col(PremiumSubscription.user_id) == col(User.id))
    .where(
        and_(
            User.status == UserStatus.MEMBER,
            User.is_premium == false(),
            PremiumSubscription.revoked_time == null(),
        )
    )
)
# Every live linked user whose token is refreshed (and TTL-measured) this run. Revoked rows are
# excluded so a deliberate disconnect never drags the alarm's Min down.
REFRESHABLE_SUBSCRIPTIONS: SelectOfScalar[PremiumSubscription] = (
    select(PremiumSubscription)
    .join(User, col(PremiumSubscription.user_id) == col(User.id))
    .where(
        and_(
            User.status == UserStatus.MEMBER,
            PremiumSubscription.revoked_time == null(),
        )
    )
)


@dataclass(frozen=True, slots=True)
class CreatorState:
    """The creator token pair to refresh this run plus the fingerprint to persist alongside it.

    ``fallback_expiration`` is the stored row's expiry (or ``None`` for a fresh adopt) used to emit
    the TTL metric when the refresh itself fails and no new expiry is available."""

    pair: TokenPair
    fingerprint: str
    fallback_expiration: dt.datetime | None


@dataclass(frozen=True, slots=True)
class RefreshSummary:
    """Per-run tallies from the user-token sweep: successful refreshes, deliberate disconnects
    (``invalid_grant`` business events), and genuine refresh faults (API/unexpected errors)."""

    refreshed: int
    revoked: int
    faults: int


def seed_fingerprint(config: PatreonConfig) -> str:
    """SHA-256 of the configured seed access token, used to detect an operator re-seed."""
    seed = config.creator_access_token.get_secret_value()
    return hashlib.sha256(seed.encode()).hexdigest()


def days_until(expiration: dt.datetime) -> float:
    """Whole and fractional days from now until ``expiration`` (negative once expired).

    Coerces a naive timestamp (as read back from the DB) to UTC so the subtraction never mixes
    aware and naive datetimes."""
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=dt.UTC)
    return (expiration - dt.datetime.now(dt.UTC)).total_seconds() / dt.timedelta(days=1).total_seconds()


@db.with_session
async def load_creator_state(session: AsyncSession, config: PatreonConfig, fingerprint: str) -> CreatorState:
    """Decide which creator pair refreshes this run: the config seed on first boot or an operator
    re-seed (fingerprint absent or changed), otherwise the DB pair, which is fresher than the seed."""
    row = (await session.exec(select(PatreonCreatorToken))).first()
    if row is None or row.seed_fingerprint != fingerprint:
        seed_pair = TokenPair(
            access_token=config.creator_access_token.get_secret_value(),
            refresh_token=config.creator_refresh_token.get_secret_value(),
            # The seed carries no expiry; refresh only reads the refresh token, so a placeholder is fine.
            expires_at=dt.datetime.now(dt.UTC),
        )
        return CreatorState(
            pair=seed_pair, fingerprint=fingerprint, fallback_expiration=row.token_expiration if row else None
        )
    stored_pair = TokenPair(
        access_token=row.access_token, refresh_token=row.refresh_token, expires_at=row.token_expiration
    )
    return CreatorState(pair=stored_pair, fingerprint=row.seed_fingerprint, fallback_expiration=row.token_expiration)


@db.with_session
async def store_creator_token(session: AsyncSession, pair: TokenPair, fingerprint: str):
    """Persist the rotated creator pair (Fernet-encrypted by the column) before it is used.

    Patreon invalidates the old pair the moment it issues a new one, so this commit must land before
    the fresh access token drives the member sweep."""
    row = (await session.exec(select(PatreonCreatorToken))).first()
    if row is None:
        session.add(
            PatreonCreatorToken(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                token_expiration=pair.expires_at,
                seed_fingerprint=fingerprint,
            )
        )
        return
    row.access_token = pair.access_token
    row.refresh_token = pair.refresh_token
    row.token_expiration = pair.expires_at
    row.seed_fingerprint = fingerprint


async def refresh_creator_token(client: TokenRefresher, config: PatreonConfig, metrics: MetricsClient) -> str | None:
    """Adopt-or-refresh the creator token, persist it, emit its TTL, and return the fresh access token.

    Returns ``None`` when the refresh is rejected with ``invalid_grant``: that cannot be auto-healed
    (recovery is re-seeding from the developer portal), so it logs an error and lets the declining
    TTL metric drive the #159 alarm rather than raising."""
    state = await load_creator_state(config, seed_fingerprint(config))
    try:
        pair = await client.refresh(state.pair)
    except PatreonTokenRevoked:
        log.error("Patreon creator token refresh rejected with invalid_grant, re-seed required")
        fallback = state.fallback_expiration
        metrics.emit(CREATOR_TOKEN_TTL_METRIC, days_until(fallback) if fallback else 0.0, MetricUnit.NONE)
        # This branch returns without raising, so the framework Fault never fires for it — this
        # counter (plus the declining TTL) is the creator failure's only CloudWatch trace.
        metrics.emit(CREATOR_REFRESH_FAULTS_METRIC, 1, MetricUnit.COUNT)
        return None
    await store_creator_token(pair, state.fingerprint)
    metrics.emit(CREATOR_TOKEN_TTL_METRIC, days_until(pair.expires_at), MetricUnit.NONE)
    metrics.emit(CREATOR_REFRESH_FAULTS_METRIC, 0, MetricUnit.COUNT)
    return pair.access_token


async def active_patreon_ids(client: CampaignMemberReader, access_token: str) -> set[str]:
    """Collect every active patron's Patreon user id across the campaign's paginated member list."""
    ids: set[str] = set()
    async for member in client.iter_campaign_members(access_token):
        if member.is_active_patron and member.patreon_user_id is not None:
            ids.add(member.patreon_user_id)
    return ids


@db.with_session
async def nominate(session: AsyncSession, statement: SelectOfScalar[PremiumSubscription]) -> list[int]:
    """Ids of the subscriptions matching a nomination sweep, read in a short read-only transaction."""
    return [subscription.db_id for subscription in (await session.exec(statement)).all()]


async def load_user(session: AsyncSession, user_id: int) -> User | None:
    """Load the notification target for a subscription; the settings relationship comes eagerly."""
    return (await session.exec(select(User).where(User.id == user_id))).first()


async def process_due_subscription(subscription_id: int, active_ids: set[str], api: TelegramApiWrapper) -> DueOutcome:
    """Advance one due subscription through the grace flow under its own write lifecycle.

    Re-checks under the fresh transaction that the row is still due; a revoked row counts as
    non-member here so a lingering campaign pledge cannot silently re-grant premium after the user
    disconnected the app. Returns the transition taken so ``run`` can tally lifecycle counts."""
    async with db.begin_write(api) as session:
        subscription = (await session.exec(DUE_SUBSCRIPTIONS.where(PremiumSubscription.id == subscription_id))).first()
        if subscription is None:
            return DueOutcome.SKIPPED
        user = await load_user(session, subscription.user_id)
        if user is None:
            return DueOutcome.SKIPPED

        is_member = subscription.revoked_time is None and subscription.patreon_user_id in active_ids
        if is_member:
            subscription.expiration_notified = False
            subscription.premium_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
            return DueOutcome.EXTENDED
        if not subscription.expiration_notified:
            subscription.expiration_notified = True
            subscription.premium_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
            await api.send_message_to_user(user, PremiumNotificationMessages.GRACE_STARTED.get(lang=user.lang))
            return DueOutcome.GRACE_STARTED
        user.is_premium = False
        await api.send_message_to_user(user, PremiumNotificationMessages.PREMIUM_LOST.get(lang=user.lang))
        return DueOutcome.PREMIUM_LOST


async def upgrade_subscription(subscription_id: int, active_ids: set[str], api: TelegramApiWrapper) -> bool:
    """Turn premium on for a linked user who has become an active patron, in its own write lifecycle.

    Returns whether an upgrade actually happened (False when the row is no longer eligible)."""
    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(UPGRADABLE_SUBSCRIPTIONS.where(PremiumSubscription.id == subscription_id))
        ).first()
        if subscription is None or subscription.patreon_user_id not in active_ids:
            return False
        user = await load_user(session, subscription.user_id)
        if user is None:
            return False
        user.is_premium = True
        subscription.premium_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
        subscription.expiration_notified = False
        await api.send_message_to_user(user, PremiumNotificationMessages.UPGRADED.get(lang=user.lang))
        return True


@db.with_session
async def load_subscription_pair(session: AsyncSession, subscription_id: int) -> TokenPair | None:
    """Read a live subscription's token pair for an out-of-transaction refresh, or ``None`` if gone."""
    subscription = (
        await session.exec(select(PremiumSubscription).where(PremiumSubscription.id == subscription_id))
    ).first()
    if subscription is None or subscription.revoked_time is not None:
        return None
    return TokenPair(
        access_token=subscription.access_token,
        refresh_token=subscription.refresh_token,
        expires_at=subscription.token_expiration,
    )


async def refresh_user_token(
    subscription_id: int, client: TokenRefresher, api: TelegramApiWrapper
) -> tuple[int, dt.datetime] | UserTokenOutcome:
    """Refresh one user token and persist it, or run the revoke-as-unlink flow on ``invalid_grant``.

    Returns ``(user_id, new_expiration)`` for the caller to emit a TTL sample, ``UserTokenOutcome.REVOKED``
    for a deliberate disconnect (excluded from the TTL series), or ``UserTokenOutcome.GONE`` when the row
    vanished. The HTTP refresh runs outside the transaction so no DB connection is held across Patreon I/O."""
    pair = await load_subscription_pair(subscription_id)
    if pair is None:
        return UserTokenOutcome.GONE

    try:
        new_pair = await client.refresh(pair)
    except PatreonTokenRevoked:
        new_pair = None

    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(REFRESHABLE_SUBSCRIPTIONS.where(PremiumSubscription.id == subscription_id))
        ).first()
        if subscription is None:
            return UserTokenOutcome.GONE
        user = await load_user(session, subscription.user_id)

        if new_pair is None:
            subscription.revoked_time = dt.datetime.now(dt.UTC)
            subscription.expiration_notified = True
            subscription.premium_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
            if user is not None:
                await api.send_message_to_user(
                    user, PremiumNotificationMessages.DISCONNECTED_RECONNECT.get(lang=user.lang)
                )
            return UserTokenOutcome.REVOKED

        subscription.access_token = new_pair.access_token
        subscription.refresh_token = new_pair.refresh_token
        subscription.token_expiration = new_pair.expires_at
        return subscription.user_id, new_pair.expires_at


async def process_all[T](handler: Callable[[int], Awaitable[T]], ids: list[int], failures: list[str]) -> list[T]:
    """Run ``handler`` over each nominated id, isolating failures so one bad row cannot abort the run.

    Returns the results of the calls that did not raise (one per processed row), so ``run`` can tally
    outcomes; a raising row is recorded in ``failures`` and skipped."""
    results: list[T] = []
    for subscription_id in ids:
        try:
            results.append(await handler(subscription_id))
        except Exception as error:
            failures.append(f"subscription {subscription_id}: {error}")
            log.exception("Premium check failed for a subscription", subscription=subscription_id, exc_info=error)
    return results


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Validate premium memberships against Patreon and keep both token families fresh.

    No-ops cleanly when Patreon is unconfigured, so the bot can deploy before any credentials exist.
    Each subscription is handled in its own write lifecycle; a mid-run failure leaves earlier commits
    intact and re-nominates the rest next run. Emits the per-run outcome counters (COUNT, EventType
    base dimension only) before any failure raise so the series stay continuous."""
    if not patreon.is_configured():
        log.info("Patreon not configured, skipping premium check")
        return

    config = patreon.current_config()
    failures: list[str] = []

    async with PatreonClient(config) as client:
        creator_access_token = await refresh_creator_token(client, config, metrics)
        if creator_access_token is None:
            # No usable creator token means no member list; the TTL alarm already covers this.
            return
        active_ids = await active_patreon_ids(client, creator_access_token)

        due_outcomes = await process_all(
            lambda subscription_id: process_due_subscription(subscription_id, active_ids, api),
            await nominate(DUE_SUBSCRIPTIONS),
            failures,
        )
        upgrade_results = await process_all(
            lambda subscription_id: upgrade_subscription(subscription_id, active_ids, api),
            await nominate(UPGRADABLE_SUBSCRIPTIONS),
            failures,
        )
        refresh = await refresh_user_tokens(client, api, metrics, failures)

    grace_started = sum(1 for outcome in due_outcomes if outcome is DueOutcome.GRACE_STARTED)
    premium_lost = sum(1 for outcome in due_outcomes if outcome is DueOutcome.PREMIUM_LOST)
    upgraded = sum(1 for upgraded_flag in upgrade_results if upgraded_flag)

    metrics.emit(GRACE_STARTED_METRIC, grace_started, MetricUnit.COUNT)
    metrics.emit(PREMIUM_LOST_METRIC, premium_lost, MetricUnit.COUNT)
    metrics.emit(UPGRADES_METRIC, upgraded, MetricUnit.COUNT)
    metrics.emit(USER_TOKENS_REFRESHED_METRIC, refresh.refreshed, MetricUnit.COUNT)
    metrics.emit(USER_TOKENS_REVOKED_METRIC, refresh.revoked, MetricUnit.COUNT)
    metrics.emit(USER_TOKEN_REFRESH_FAULTS_METRIC, refresh.faults, MetricUnit.COUNT)

    if failures:
        raise RuntimeError(f"Premium check failed for {len(failures)} subscriptions. Check logs for details.")

    log.info(
        "premium check complete",
        due_processed=len(due_outcomes),
        grace_started=grace_started,
        premium_lost=premium_lost,
        upgraded=upgraded,
        tokens_refreshed=refresh.refreshed,
        tokens_revoked=refresh.revoked,
        active_patrons=len(active_ids),
    )


async def refresh_user_tokens(
    client: PatreonClient, api: TelegramApiWrapper, metrics: MetricsClient, failures: list[str]
) -> RefreshSummary:
    """Refresh every live user token, emitting one dimensionless TTL sample per surviving row.

    Returns the run tallies (refreshed / revoked / faults). ``UserId`` rides as an EMF property (not a
    dimension) so the ``Min`` over the single series is the fleet's worst token and Logs Insights can
    name the user. A deliberate ``invalid_grant`` revocation is a business event, not a fault."""
    refreshed = 0
    revoked = 0
    faults = 0
    for subscription_id in await nominate(REFRESHABLE_SUBSCRIPTIONS):
        try:
            result = await refresh_user_token(subscription_id, client, api)
        except Exception as error:
            failures.append(f"subscription {subscription_id}: {error}")
            faults += 1
            log.exception("User token refresh failed", subscription=subscription_id, exc_info=error)
            continue
        if result is UserTokenOutcome.REVOKED:
            revoked += 1
        elif result is UserTokenOutcome.GONE:
            continue
        else:
            user_id, expiration = result
            metrics.emit(USER_TOKEN_TTL_METRIC, days_until(expiration), MetricUnit.NONE, properties={"UserId": user_id})
            refreshed += 1
    return RefreshSummary(refreshed=refreshed, revoked=revoked, faults=faults)
