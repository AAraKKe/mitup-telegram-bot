"""Daily supporter-membership validation against Patreon.

Plugs into the recurrent-events framework as the ``SUPPORTER_CHECK`` job. Each run refreshes the single
creator token, sweeps the campaign roster it grants access to, and reconciles every linked member's
supporter tier against the amount they are currently entitled to on the campaign, driving the
grace/upgrade/downgrade/revoke transitions. A member's tier is derived from their
``currently_entitled_amount_cents`` via ``supporter.level_for_amount``; both between-tier upgrades and
between-tier downgrades sync on this daily pass, while a full lapse follows the existing grace/revoke
machinery. The creator token-TTL metric feeds the infra alarms, so its name is pinned as a
literal string rather than routed through ``MetricKey`` (whose CamelCase folding would lowercase the
``TTL`` acronym).
"""

import datetime as dt
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum, auto
from typing import Protocol

import structlog
from sqlmodel import and_, col, func, null, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db, patreon, supporter
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.patreon import PatreonClient
from mitup_bot.patreon.creator_token import TokenRefresher, load_creator_state, seed_fingerprint, store_creator_token
from mitup_bot.patreon.models import MemberResource
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages

log = structlog.get_logger(__name__)


class CampaignMemberReader(Protocol):
    """The campaign-listing capability the member sweep depends on."""

    def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]: ...


# Pinned metric name for the alarm. Kept as a literal because the CamelCaseStrEnum used by
# MetricKey would emit "PatreonCreatorTokenTtl", breaking the alarm's exact-name match.
CREATOR_TOKEN_TTL_METRIC = "PatreonCreatorTokenTTL"

# Per-run outcome counters (same literal-name rationale as the TTL metric above). Dashboard/diagnosis
# material, not a paging surface: they ride the run's MetricsClient with its EventType=SupporterCheck base
# dimension only (one series per name). Emitted every run — zeros included — so the series stay continuous.
CREATOR_REFRESH_FAULTS_METRIC = "PatreonCreatorRefreshFaults"
UPGRADES_METRIC = "PatreonUpgrades"
DOWNGRADES_METRIC = "PatreonDowngrades"
GRACE_STARTED_METRIC = "PatreonGraceStarted"
SUPPORT_LOST_METRIC = "PatreonSupportLost"


class DueOutcome(Enum):
    """What a due subscription's grace-flow pass did, so ``run`` can aggregate lifecycle counts."""

    EXTENDED = auto()
    GRACE_STARTED = auto()
    SUPPORT_LOST = auto()
    SKIPPED = auto()


class LevelSyncOutcome(Enum):
    """What a level-sync pass did for one active member, so ``run`` can tally tier movements."""

    UPGRADED = auto()
    DOWNGRADED = auto()
    UNCHANGED = auto()
    SKIPPED = auto()


# Supporters keep their perks for a week past each unconfirmed checkpoint, so a lapsed or
# disconnected patron gets ~two weeks of leeway (grace start, then the grace expiry) before revocation.
GRACE_PERIOD = dt.timedelta(days=7)

# Nomination sweeps: read-only, ids only. Each nominated subscription is re-loaded and re-checked
# inside its own write lifecycle, so nothing read here feeds a mutation directly.
DUE_SUBSCRIPTIONS: SelectOfScalar[SupporterSubscription] = (
    select(SupporterSubscription)
    .join(User, col(SupporterSubscription.user_id) == col(User.id))
    .where(
        and_(
            User.status == UserStatus.MEMBER,
            SupporterSubscription.support_expiration != null(),
            SupporterSubscription.support_expiration <= func.now(),
        )
    )
)
# Every live linked member (status MEMBER). Feeds the level sync, which re-levels active
# members against their entitled amount — NONE -> tier promotion and between-tier moves in both
# directions; members absent from the amounts map are lapsing and left to the grace flow above.
LIVE_LINKED_SUBSCRIPTIONS: SelectOfScalar[SupporterSubscription] = (
    select(SupporterSubscription)
    .join(User, col(SupporterSubscription.user_id) == col(User.id))
    .where(User.status == UserStatus.MEMBER)
)


def days_until(expiration: dt.datetime) -> float:
    """Whole and fractional days from now until ``expiration`` (negative once expired).

    Coerces a naive timestamp (as read back from the DB) to UTC so the subtraction never mixes
    aware and naive datetimes."""
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=dt.UTC)
    return (expiration - dt.datetime.now(dt.UTC)).total_seconds() / dt.timedelta(days=1).total_seconds()


async def refresh_creator_token(client: TokenRefresher, config: PatreonConfig, metrics: MetricsClient) -> str | None:
    """Adopt-or-refresh the creator token, persist it, emit its TTL, and return the fresh access token.

    Returns ``None`` when the refresh is rejected with ``invalid_grant``: that cannot be auto-healed
    (recovery is re-seeding from the developer portal), so it logs an error and lets the declining
    TTL metric drive the alarm rather than raising."""
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


async def active_patreon_amounts(client: CampaignMemberReader, access_token: str) -> dict[str, int]:
    """Map each active patron's Patreon user id to their ``currently_entitled_amount_cents`` across the
    campaign's paginated member list. The amount is what drives the per-member tier derivation; a
    plain membership check is just ``patreon_user_id in amounts``."""
    amounts: dict[str, int] = {}
    async for member in client.iter_campaign_members(access_token):
        if member.is_active_patron and member.patreon_user_id is not None:
            amounts[member.patreon_user_id] = member.attributes.currently_entitled_amount_cents
    return amounts


@db.with_session
async def nominate(session: AsyncSession, statement: SelectOfScalar[SupporterSubscription]) -> list[int]:
    """Ids of the subscriptions matching a nomination sweep, read in a short read-only transaction."""
    return [subscription.db_id for subscription in (await session.exec(statement)).all()]


async def load_user(session: AsyncSession, user_id: int) -> User | None:
    """Load the notification target for a subscription; the settings relationship comes eagerly."""
    return (await session.exec(select(User).where(User.id == user_id))).first()


async def process_due_subscription(
    subscription_id: int, active_amounts: dict[str, int], api: TelegramApiWrapper
) -> DueOutcome:
    """Advance one due subscription through the grace flow under its own write lifecycle.

    Owns the lapse lifecycle only: for a still-active member it extends grace (the level itself is
    reconciled by the level-sync pass), and for a lapsed member it starts grace and then revokes to
    NONE. Re-checks under the fresh transaction that the row is still due. Returns the transition
    taken so ``run`` can tally lifecycle counts."""
    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(DUE_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription_id))
        ).first()
        if subscription is None:
            return DueOutcome.SKIPPED
        user = await load_user(session, subscription.user_id)
        if user is None:
            return DueOutcome.SKIPPED

        is_member = subscription.patreon_user_id in active_amounts
        if is_member:
            subscription.expiration_notified = False
            subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
            return DueOutcome.EXTENDED
        if not subscription.expiration_notified:
            subscription.expiration_notified = True
            subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
            await api.send_message_to_user(user, SupporterNotificationMessages.GRACE_STARTED.get(lang=user.lang))
            return DueOutcome.GRACE_STARTED
        user.supporter_level = SupporterLevel.NONE
        await api.send_message_to_user(user, SupporterNotificationMessages.SUPPORT_LOST.get(lang=user.lang))
        return DueOutcome.SUPPORT_LOST


async def sync_subscription_level(
    subscription_id: int, active_amounts: dict[str, int], config: PatreonConfig, api: TelegramApiWrapper
) -> LevelSyncOutcome:
    """Reconcile one active member's supporter tier with their entitled amount, in its own write
    lifecycle.

    Derives the target tier from ``currently_entitled_amount_cents`` and writes it when it differs,
    refreshing grace on any change (the member is confirmed active this run). A rank increase notifies
    the user with the generic upgrade message; a between-tier drop (still an active supporter) adjusts
    silently — per-level copy is a later phase. A member absent from the amounts map is lapsing and
    left to the grace flow, so this returns SKIPPED for them."""
    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(LIVE_LINKED_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription_id))
        ).first()
        if subscription is None:
            return LevelSyncOutcome.SKIPPED
        amount = active_amounts.get(subscription.patreon_user_id)
        if amount is None:
            return LevelSyncOutcome.SKIPPED
        user = await load_user(session, subscription.user_id)
        if user is None:
            return LevelSyncOutcome.SKIPPED

        target = supporter.level_for_amount(amount, config)
        if user.supporter_level == target:
            return LevelSyncOutcome.UNCHANGED

        previous = user.supporter_level
        user.supporter_level = target
        subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
        subscription.expiration_notified = False
        if not supporter.meets(previous, target):
            await api.send_message_to_user(user, SupporterNotificationMessages.UPGRADED.get(lang=user.lang))
            return LevelSyncOutcome.UPGRADED
        return LevelSyncOutcome.DOWNGRADED


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
            log.exception("Supporter check failed for a subscription", subscription=subscription_id, exc_info=error)
    return results


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Validate supporter memberships against Patreon and keep the creator token fresh.

    No-ops cleanly when Patreon is unconfigured, so the bot can deploy before any credentials exist.
    Each subscription is handled in its own write lifecycle; a mid-run failure leaves earlier commits
    intact and re-nominates the rest next run. Emits the per-run outcome counters (COUNT, EventType
    base dimension only) before any failure raise so the series stay continuous."""
    if not patreon.is_configured():
        log.info("Patreon not configured, skipping supporter check")
        return

    config = patreon.current_config()
    failures: list[str] = []

    async with PatreonClient(config) as client:
        creator_access_token = await refresh_creator_token(client, config, metrics)
        if creator_access_token is None:
            # No usable creator token means no member list; the TTL alarm already covers this.
            return
        active_amounts = await active_patreon_amounts(client, creator_access_token)

        due_outcomes = await process_all(
            lambda subscription_id: process_due_subscription(subscription_id, active_amounts, api),
            await nominate(DUE_SUBSCRIPTIONS),
            failures,
        )
        sync_outcomes = await process_all(
            lambda subscription_id: sync_subscription_level(subscription_id, active_amounts, config, api),
            await nominate(LIVE_LINKED_SUBSCRIPTIONS),
            failures,
        )

    grace_started = sum(1 for outcome in due_outcomes if outcome is DueOutcome.GRACE_STARTED)
    support_lost = sum(1 for outcome in due_outcomes if outcome is DueOutcome.SUPPORT_LOST)
    upgraded = sum(1 for outcome in sync_outcomes if outcome is LevelSyncOutcome.UPGRADED)
    downgraded = sum(1 for outcome in sync_outcomes if outcome is LevelSyncOutcome.DOWNGRADED)

    metrics.emit(GRACE_STARTED_METRIC, grace_started, MetricUnit.COUNT)
    metrics.emit(SUPPORT_LOST_METRIC, support_lost, MetricUnit.COUNT)
    metrics.emit(UPGRADES_METRIC, upgraded, MetricUnit.COUNT)
    metrics.emit(DOWNGRADES_METRIC, downgraded, MetricUnit.COUNT)

    if failures:
        raise RuntimeError(f"Supporter check failed for {len(failures)} subscriptions. Check logs for details.")

    log.info(
        "supporter check complete",
        due_processed=len(due_outcomes),
        grace_started=grace_started,
        support_lost=support_lost,
        upgraded=upgraded,
        downgraded=downgraded,
        active_patrons=len(active_amounts),
    )
