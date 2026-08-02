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
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol

import structlog
from sqlmodel import and_, col, func, null, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db, hosts_group, patreon, supporter
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonTokenRevoked
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient, MetricUnit
from mitup_bot.patreon import PatreonClient
from mitup_bot.patreon.creator_token import (
    REFRESH_REMEDIATION,
    TokenRefresher,
    load_creator_state,
    seed_fingerprint,
    store_creator_token,
)
from mitup_bot.patreon.models import MemberResource
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages
from mitup_bot.views.collaborate import hosts_group_removed_view

from .telemetry import error_type_name

log = structlog.get_logger(__name__)


class CampaignMemberReader(Protocol):
    """The campaign-listing capability the member sweep depends on."""

    def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]: ...


# Pinned metric name for the alarm. Kept as a literal because the CamelCaseStrEnum used by
# MetricKey would emit "PatreonCreatorTokenTtl", breaking the alarm's exact-name match.
CREATOR_TOKEN_TTL_METRIC = "PatreonCreatorTokenTTL"

# A creator-token refresh rejection returns rather than raising, so the framework Fault never fires
# for it: this counter is its own alarm surface. Emitted every run — zeros included — so the series
# is continuous rather than a failure-only signal.
CREATOR_REFRESH_FAULTS_METRIC = "PatreonCreatorRefreshFaults"
# How many people are paying right now: state of the world, read as a gauge rather than as run
# activity, which is why it stays a series while the run's outcome counts ride the summary line.
ACTIVE_PATRONS_METRIC = "PatreonActivePatrons"

# Machine-readable reason on the log line for a run that reconciled nothing, so log queries key on a
# field rather than on the event text.
ABORT_REASON_CREATOR_TOKEN_UNUSABLE = "creator_token_unusable"


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


@dataclass
class RunTally:
    """What one run did, aggregated for the active-patron gauge and the completion log.

    The defaults describe a run that reconciled nothing, so the gauge can be emitted from any exit
    path — including an abort before the sweep starts — and the series lands a datapoint."""

    due_outcomes: list[DueOutcome] = field(default_factory=list)
    sync_outcomes: list[LevelSyncOutcome] = field(default_factory=list)
    # Ids, not rendered sentences: the failing rows have to be filterable and joinable to a person.
    failures: list[int] = field(default_factory=list)
    active_patrons: int = 0

    def count_due(self, outcome: DueOutcome) -> int:
        return sum(1 for candidate in self.due_outcomes if candidate is outcome)

    def count_synced(self, outcome: LevelSyncOutcome) -> int:
        return sum(1 for candidate in self.sync_outcomes if candidate is outcome)

    def emit_gauges(self, metrics: MetricsClient):
        metrics.emit(ACTIVE_PATRONS_METRIC, self.active_patrons, MetricUnit.COUNT)


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


@dataclass(frozen=True, slots=True)
class CreatorTokenRefresh:
    """Outcome of one creator-token refresh.

    ``access_token`` is ``None`` when Patreon rejected the refresh; ``ttl_days`` then carries what is
    left on the stored pair (0.0 when none is stored) instead of the fresh token's lifetime."""

    access_token: str | None
    ttl_days: float


async def refresh_creator_token(
    client: TokenRefresher, config: PatreonConfig, metrics: MetricsClient
) -> CreatorTokenRefresh:
    """Adopt-or-refresh the creator token, persist it, emit its TTL, and return the fresh access token.

    Comes back with no access token when the refresh is rejected with ``invalid_grant``: that cannot be
    auto-healed (recovery is re-seeding from the developer portal), so it logs an error and lets the
    declining TTL metric drive the alarm rather than raising."""
    state = await load_creator_state(config, seed_fingerprint(config))
    try:
        pair = await client.refresh(state.pair)
    except PatreonTokenRevoked:
        fallback = state.fallback_expiration
        stored_ttl_days = days_until(fallback) if fallback else 0.0
        log.error(
            "Patreon creator token refresh rejected, re-seed required",
            reason="invalid_grant",
            source=str(state.source),
            fallback_expiration=fallback,
            ttl_days=stored_ttl_days,
            remediation=REFRESH_REMEDIATION,
        )
        metrics.emit(CREATOR_TOKEN_TTL_METRIC, stored_ttl_days, MetricUnit.NONE)
        # This branch returns without raising, so the framework Fault never fires for it — this
        # counter and the declining TTL are what mark the creator failure itself in CloudWatch.
        metrics.emit(CREATOR_REFRESH_FAULTS_METRIC, 1, MetricUnit.COUNT)
        return CreatorTokenRefresh(access_token=None, ttl_days=stored_ttl_days)
    await store_creator_token(pair, state.fingerprint)
    ttl_days = days_until(pair.expires_at)
    metrics.emit(CREATOR_TOKEN_TTL_METRIC, ttl_days, MetricUnit.NONE)
    metrics.emit(CREATOR_REFRESH_FAULTS_METRIC, 0, MetricUnit.COUNT)
    log.info("Patreon creator token refreshed", ttl_days=ttl_days)
    return CreatorTokenRefresh(access_token=pair.access_token, ttl_days=ttl_days)


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


async def remove_from_hosts_group(api: TelegramApiWrapper, user: User):
    """Ban a revoked host from the hosts-only group, unless they are the group creator/administrator.

    banChatMember cannot ban the creator and fails on other admins, so an admin is skipped rather
    than attempted. The removal DM is sent only when the user was actually in the group, so a lapsed
    host who never joined is not told they were removed. A no-op when the feature is unconfigured. The
    api wrapper swallows Telegram failures, so this never aborts the daily job."""
    chat_id = hosts_group.chat_id()
    if chat_id is None:
        return
    if await api.is_chat_admin(chat_id, user.tg_user_id):
        log.info("Skipping hosts-group removal for a group admin", tg_user_id=user.tg_user_id, chat_id=chat_id)
        return
    was_member = await api.is_chat_member(chat_id, user.tg_user_id)
    await api.ban_chat_member(chat_id, user.tg_user_id)
    log.info("Removed lapsed host from the hosts-only group", tg_user_id=user.tg_user_id, chat_id=chat_id)
    if was_member:
        await api.send_message_to_user(user, hosts_group_removed_view(user.lang))


async def advance_grace_flow(
    api: TelegramApiWrapper, subscription: SupporterSubscription, user: User, active_amounts: dict[str, int]
) -> DueOutcome:
    """Move one due subscription one step along the lapse lifecycle and record which step it was.

    The subscription and user ids are on the ambient bind, so each line names only what its own
    decision turned on.
    """
    if subscription.patreon_user_id in active_amounts:
        subscription.expiration_notified = False
        subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
        log.info("Supporter grace extended", grace_until=subscription.support_expiration, reason="still_active_patron")
        return DueOutcome.EXTENDED

    if not subscription.expiration_notified:
        subscription.expiration_notified = True
        subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
        await api.send_message_to_user(user, SupporterNotificationMessages.GRACE_STARTED.get(lang=user.lang))
        log.info("Supporter grace started", grace_until=subscription.support_expiration, reason="not_in_active_patrons")
        return DueOutcome.GRACE_STARTED

    previous = user.supporter_level
    user.supporter_level = SupporterLevel.NONE
    log.info(
        "Supporter level revoked",
        patreon_user_id=subscription.patreon_user_id,
        previous_level=previous.value,
        new_level=SupporterLevel.NONE.value,
        reason="grace_expired_and_not_active_patron",
    )
    await api.send_message_to_user(user, SupporterNotificationMessages.SUPPORT_LOST.get(lang=user.lang))
    await remove_from_hosts_group(api, user)
    return DueOutcome.SUPPORT_LOST


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
            log.info("Due subscription skipped", reason="no_longer_due")
            return DueOutcome.SKIPPED
        user = await load_user(session, subscription.user_id)
        if user is None:
            log.info("Due subscription skipped", reason="user_row_missing")
            return DueOutcome.SKIPPED

        with structlog.contextvars.bound_contextvars(user_id=user.db_id, tg_user_id=user.tg_user_id):
            return await advance_grace_flow(api, subscription, user, active_amounts)


async def apply_target_level(
    api: TelegramApiWrapper, subscription: SupporterSubscription, user: User, amount: int, config: PatreonConfig
) -> LevelSyncOutcome:
    """Write the tier the entitled amount buys, tell the user, and record the move.

    The unchanged case is logged too: it is bounded by the number of linked accounts, and it is the
    only evidence that a tier was checked and deliberately left alone.
    """
    previous = user.supporter_level
    target = supporter.level_for_amount(amount, config)
    if previous == target:
        log.info(
            "Supporter level unchanged",
            supporter_level=previous.value,
            amount_cents=amount,
            reason="entitled_amount_matches_level",
        )
        return LevelSyncOutcome.UNCHANGED

    user.supporter_level = target
    subscription.support_expiration = dt.datetime.now(dt.UTC) + GRACE_PERIOD
    subscription.expiration_notified = False
    upgraded = not supporter.meets(previous, target)
    log.info(
        "Supporter level changed",
        previous_level=previous.value,
        new_level=target.value,
        amount_cents=amount,
        direction="upgrade" if upgraded else "downgrade",
        reason="entitled_amount_changed",
    )
    message = (
        SupporterNotificationMessages.unlocked_for(target)
        if upgraded
        else SupporterNotificationMessages.downgraded_to(target)
    )
    await api.send_message_to_user(user, message.get(lang=user.lang))
    return LevelSyncOutcome.UPGRADED if upgraded else LevelSyncOutcome.DOWNGRADED


async def sync_subscription_level(
    subscription_id: int, active_amounts: dict[str, int], config: PatreonConfig, api: TelegramApiWrapper
) -> LevelSyncOutcome:
    """Reconcile one active member's supporter tier with their entitled amount, in its own write
    lifecycle.

    Derives the target tier from ``currently_entitled_amount_cents`` and writes it when it differs,
    refreshing grace on any change (the member is confirmed active this run). A rank increase notifies
    the user with the per-tier unlock message; a between-tier drop (still an active supporter) notifies
    with the neutral per-tier tier-set message. A member absent from the amounts map is lapsing and
    left to the grace flow, so this returns SKIPPED for them."""
    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(LIVE_LINKED_SUBSCRIPTIONS.where(SupporterSubscription.id == subscription_id))
        ).first()
        if subscription is None:
            log.info("Level sync skipped", reason="subscription_missing")
            return LevelSyncOutcome.SKIPPED
        amount = active_amounts.get(subscription.patreon_user_id)
        if amount is None:
            log.info("Level sync skipped", reason="not_in_active_patrons")
            return LevelSyncOutcome.SKIPPED
        user = await load_user(session, subscription.user_id)
        if user is None:
            log.info("Level sync skipped", reason="user_row_missing")
            return LevelSyncOutcome.SKIPPED

        with structlog.contextvars.bound_contextvars(user_id=user.db_id, tg_user_id=user.tg_user_id):
            return await apply_target_level(api, subscription, user, amount, config)


async def process_all[T](handler: Callable[[int], Awaitable[T]], ids: list[int], failures: list[int]) -> list[T]:
    """Run ``handler`` over each nominated id, isolating failures so one bad row cannot abort the run.

    Returns the results of the calls that did not raise (one per processed row), so ``run`` can tally
    outcomes; a raising row is recorded in ``failures`` and skipped. The subscription id is bound for
    the duration of each pass, so every line the handler and the api wrapper emit under it — the
    hosts-group removal, the send warnings, the failure below — names the row it belongs to."""
    results: list[T] = []
    for subscription_id in ids:
        with structlog.contextvars.bound_contextvars(subscription_id=subscription_id):
            try:
                results.append(await handler(subscription_id))
            except Exception as error:
                failures.append(subscription_id)
                log.exception(
                    "Supporter check failed for a subscription",
                    reason="subscription_processing_failed",
                    error_type=error_type_name(error),
                    exc_info=error,
                )
    return results


async def reconcile_memberships(api: TelegramApiWrapper, metrics: MetricsClient, tally: RunTally) -> bool:
    """Refresh the creator token, sweep the campaign roster, and reconcile every linked member,
    recording each pass's outcomes and failures in ``tally``.

    Returns False when an unusable creator token stopped the run before any membership was looked at."""
    config = patreon.current_config()
    async with PatreonClient(config) as client:
        refresh = await refresh_creator_token(client, config, metrics)
        if refresh.access_token is None:
            # Re-seeding from the developer portal is the only recovery, and the TTL alarm breaches
            # only once the stored token decays past its threshold, days later — so this line and the
            # zero counters are what mark a run that reconciled nothing.
            log.error(
                "Supporter check aborted before reconciling any membership",
                reason=ABORT_REASON_CREATOR_TOKEN_UNUSABLE,
                creator_token_ttl_days=refresh.ttl_days,
            )
            return False

        active_amounts = await active_patreon_amounts(client, refresh.access_token)
        tally.active_patrons = len(active_amounts)
        log.info("Fetched active Patreon members", count=len(active_amounts))

        due_ids = await nominate(DUE_SUBSCRIPTIONS)
        log.info("Nominated due subscriptions", count=len(due_ids))
        tally.due_outcomes = await process_all(
            lambda subscription_id: process_due_subscription(subscription_id, active_amounts, api),
            due_ids,
            tally.failures,
        )
        live_ids = await nominate(LIVE_LINKED_SUBSCRIPTIONS)
        log.info("Nominated live linked subscriptions", count=len(live_ids))
        tally.sync_outcomes = await process_all(
            lambda subscription_id: sync_subscription_level(subscription_id, active_amounts, config, api),
            live_ids,
            tally.failures,
        )
    return True


def log_run_summary(tally: RunTally, reconciled: bool):
    """Record what the run did, on every exit path.

    Runs from ``run``'s ``finally`` alongside ``emit_gauges``, so a partially-failed run lands a
    summary next to its continuous counters rather than leaving the counters as the only account of
    it. A run that aborted before reconciling anything is described by its abort line instead."""
    if tally.failures:
        log.warning(
            "Supporter check finished with failures",
            count=len(tally.failures),
            subscription_ids=tally.failures,
            reason="per_subscription_processing_failed",
        )
    if not reconciled:
        return
    log.info(
        "Supporter check complete",
        due_processed=len(tally.due_outcomes),
        extended=tally.count_due(DueOutcome.EXTENDED),
        grace_started=tally.count_due(DueOutcome.GRACE_STARTED),
        support_lost=tally.count_due(DueOutcome.SUPPORT_LOST),
        upgraded=tally.count_synced(LevelSyncOutcome.UPGRADED),
        downgraded=tally.count_synced(LevelSyncOutcome.DOWNGRADED),
        active_patrons=tally.active_patrons,
        subscription_faults=len(tally.failures),
    )


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Validate supporter memberships against Patreon and keep the creator token fresh.

    Each subscription is handled in its own write lifecycle; a mid-run failure leaves earlier commits
    intact and re-nominates the rest next run. The active-patron gauge is emitted from a ``finally``,
    so an aborting or raising run still lands a datapoint and the series stays continuous. What the
    run did rides the ``Supporter check complete`` line."""
    tally = RunTally()
    reconciled = False
    try:
        reconciled = await reconcile_memberships(api, metrics, tally)
    finally:
        tally.emit_gauges(metrics)
        log_run_summary(tally, reconciled)

    if tally.failures:
        raise RuntimeError(f"Supporter check failed for {len(tally.failures)} subscriptions. Check logs for details.")
