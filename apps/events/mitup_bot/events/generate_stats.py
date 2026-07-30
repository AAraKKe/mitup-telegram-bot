from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Integer, and_, cast, col, distinct, false, func, literal, null, or_, select, true
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.acquisition import ACQUISITION_SOURCE_DIMENSION, AcquisitionSource, normalize_acquisition_source
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.db import with_session
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import SUPPORTED_LANGUAGES

log = structlog.get_logger(__name__)

# Pinned literal names: MetricKey's CamelCase folding would render the trailing "24h" as "24H",
# and the dashboard widgets match these exact names.
NEW_USERS_24H_METRIC = "NewUsers24h"
MEETINGS_CREATED_24H_METRIC = "MeetingsCreated24h"
MEETINGS_FINISHED_24H_METRIC = "MeetingsFinished24h"
MEETING_JOINS_24H_METRIC = "MeetingJoins24h"
INVITED_JOINS_24H_METRIC = "InvitedJoins24h"
UPCOMING_MEETINGS_24H_METRIC = "UpcomingMeetings24h"

# Per-tier supporter gauges follow the same name-prefix pattern as the per-language user gauges
# ("Supporters/host_1", ...), so a dashboard SEARCH widget auto-discovers any future tier.
SUPPORTERS_METRIC_PREFIX = "Supporters"

# `Settings.language` is a plain string column with no database constraint, so what the write path
# accepts today does not bound what the table holds tomorrow: dropping or renaming a locale in
# SUPPORTED_LANGUAGES turns every row still carrying it unsupported at once, with no migration
# needed to make it happen. Those rows are counted here rather than under their own name, because a
# metric name is a billed CloudWatch series forever and this job reads its prefixes from the
# database.
OTHER_LANGUAGE_BUCKET = "Other"

PAST_24H = func.now() - func.cast(literal("24 hours"), INTERVAL)
NEXT_24H = func.now() + func.cast(literal("24 hours"), INTERVAL)


def zero_coerced(result: Sequence[Any] | None, width: int, query: str) -> list[int]:
    """Read an aggregate row as ints, warning when the query matched no row at all.

    An empty table sums to NULL across zero rows, so 0 is the truthful statistic for every gauge —
    but a system with no users and a query that silently stopped matching produce the same zeros,
    and the warning is the only thing that tells them apart.
    """
    if result is None:
        log.warning("Stats query returned no rows", query=query, reason="empty_result_set")
        return [0] * width
    return [value or 0 for value in result]


async def users_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    member_users = func.sum(cast(User.status == UserStatus.MEMBER, Integer))
    left_users = func.sum(cast(User.status == UserStatus.LEFT, Integer))
    joined_only_users = func.sum(cast(and_(User.status == UserStatus.JOINED_ONLY, User.tg_user_id != -1), Integer))
    deletion_requested_users = func.sum(cast(User.status == UserStatus.DELETION_REQUESTED, Integer))
    total_users = func.count()
    invited_users = func.sum(cast(User.tg_user_id == -1, Integer))
    new_users_24h = func.sum(cast(User.created_time > PAST_24H, Integer))
    result = (
        await session.exec(
            select(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1333
                member_users,
                left_users,
                joined_only_users,
                deletion_requested_users,
                total_users,
                invited_users,
                new_users_24h,
            )
        )
    ).first()

    counts = zero_coerced(result, 7, query="users")

    metrics.emit(MetricKey.ACTIVE_USERS, counts[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.INACTIVE_USERS, counts[1], MetricUnit.COUNT)
    metrics.emit(MetricKey.JOINED_ONLY_USERS, counts[2], MetricUnit.COUNT)
    metrics.emit(MetricKey.DELETION_REQUESTED_USERS, counts[3], MetricUnit.COUNT)
    metrics.emit(MetricKey.TOTAL_USERS, counts[4], MetricUnit.COUNT)
    metrics.emit(MetricKey.INVITED_USERS, counts[5], MetricUnit.COUNT)
    metrics.emit(NEW_USERS_24H_METRIC, counts[6], MetricUnit.COUNT)

    # Per-language gauges count MEMBER users only, so the language series sum to ActiveUsers. Every
    # supported language reports every run, zero included, so each series is gap-free.
    user_languages = (
        await session.exec(
            select(Settings.language, func.count())
            .join(User, col(Settings.user_id) == col(User.id))
            .where(User.status == UserStatus.MEMBER)
            .group_by(Settings.language)
        )
    ).all()

    counts_by_language = dict.fromkeys([*SUPPORTED_LANGUAGES, OTHER_LANGUAGE_BUCKET], 0)
    for language, count in user_languages:
        counts_by_language[language if language in SUPPORTED_LANGUAGES else OTHER_LANGUAGE_BUCKET] += count

    for language, count in counts_by_language.items():
        metrics.emit(MetricKey.ACTIVE_USERS.with_prefix(language), count, MetricUnit.COUNT)

    return {
        "member_users": counts[0],
        "left_users": counts[1],
        "joined_only_users": counts[2],
        "deletion_requested_users": counts[3],
        "total_users": counts[4],
        "invited_users": counts[5],
        "new_users_24h": counts[6],
        "member_users_by_language": counts_by_language,
    }


async def new_members_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    """Count the users who completed onboarding in the last 24 hours, by where they arrived from.

    Grouping happens in SQL on the raw stored value and normalization in Python, so the query stays
    one row per distinct payload — a bounded number of them — while the dimension only ever carries
    a value from the closed vocabulary. Grouping on a normalized expression is not an option: the
    raw column is user text, and mapping it in SQL would duplicate the rules.
    """
    members_by_raw_source = (
        await session.exec(
            select(User.acquisition_source, func.count())
            .where(User.member_time > PAST_24H)
            .group_by(User.acquisition_source)
        )
    ).all()

    counts_by_source = dict.fromkeys(AcquisitionSource, 0)
    for raw_source, count in members_by_raw_source:
        counts_by_source[normalize_acquisition_source(raw_source)] += count

    # Every source reports every run, zero included: a sparse series cannot distinguish a channel
    # that dried up from one nobody has queried yet, and only a real zero can be alarmed on.
    for source, count in counts_by_source.items():
        metrics.emit(
            MetricKey.NEW_MEMBERS, count, MetricUnit.COUNT, dimensions={ACQUISITION_SOURCE_DIMENSION: source.value}
        )

    return {"new_members_24h_by_source": {source.value: count for source, count in counts_by_source.items()}}


async def meetings_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    active_meetings = func.sum(cast(Meetup.active, Integer))
    total_meetings = func.count()
    # The per-facet gauges cover ACTIVE meetings only: they read as feature adoption, not as
    # ever-growing all-time totals.
    incognito_meetings = func.sum(cast(and_(Meetup.active, Meetup.incognito), Integer))
    public_meetings = func.sum(cast(and_(Meetup.active, Meetup.public), Integer))
    meetings_with_invitation = func.sum(cast(and_(Meetup.active, Meetup.allow_invitation), Integer))
    meetings_with_datetime = func.sum(cast(and_(Meetup.active, Meetup.datetime != null()), Integer))

    result = (
        await session.exec(
            select(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1333
                active_meetings,
                total_meetings,
                incognito_meetings,
                public_meetings,
                meetings_with_invitation,
                meetings_with_datetime,
            )
        )
    ).first()

    counts = zero_coerced(result, 6, query="meetings")

    metrics.emit(MetricKey.ACTIVE_MEETINGS, counts[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.TOTAL_MEETINGS, counts[1], MetricUnit.COUNT)
    metrics.emit(MetricKey.INCOGNITO_MEETINGS, counts[2], MetricUnit.COUNT)
    metrics.emit(MetricKey.PUBLIC_MEETINGS, counts[3], MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_WITH_INVITATION, counts[4], MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_WITH_DATETIME, counts[5], MetricUnit.COUNT)

    # Get number of shared meetings through inline messages
    shared_meetings = func.count(distinct(Message.meetup_id))
    count_result = (await session.exec(select(shared_meetings).where(Message.inline_message_id != null()))).first()

    if count_result is None:
        # It can happen that there are no meetings shared
        count_result = 0

    metrics.emit(MetricKey.SHARED_MEETINGS, count_result, MetricUnit.COUNT)

    return {
        "active_meetings": counts[0],
        "total_meetings": counts[1],
        "incognito_meetings": counts[2],
        "public_meetings": counts[3],
        "meetings_with_invitation": counts[4],
        "meetings_with_datetime": counts[5],
        "shared_meetings": count_result,
    }


async def meeting_activity_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    created_24h = func.sum(cast(Meetup.created_time > PAST_24H, Integer))
    # `expiration_time` is stamped at deactivation, so a recent value means a recently finished meeting.
    finished_24h = func.sum(cast(Meetup.expiration_time > PAST_24H, Integer))
    upcoming_24h = func.sum(
        cast(and_(Meetup.active, Meetup.datetime > func.now(), Meetup.datetime <= NEXT_24H), Integer)
    )
    in_progress = func.sum(
        cast(
            and_(
                Meetup.active,
                Meetup.datetime <= func.now(),
                or_(Meetup.end_datetime == null(), Meetup.end_datetime > func.now()),
            ),
            Integer,
        )
    )

    result = (await session.exec(select(created_24h, finished_24h, upcoming_24h, in_progress))).first()

    counts = zero_coerced(result, 4, query="meeting_activity")

    metrics.emit(MEETINGS_CREATED_24H_METRIC, counts[0], MetricUnit.COUNT)
    metrics.emit(MEETINGS_FINISHED_24H_METRIC, counts[1], MetricUnit.COUNT)
    metrics.emit(UPCOMING_MEETINGS_24H_METRIC, counts[2], MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_IN_PROGRESS, counts[3], MetricUnit.COUNT)

    return {
        "meetings_created_24h": counts[0],
        "meetings_finished_24h": counts[1],
        "upcoming_meetings_24h": counts[2],
        "meetings_in_progress": counts[3],
    }


async def participation_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    joins_24h = func.sum(cast(JoinedUsers.created_time > PAST_24H, Integer))
    invited_joins_24h = func.sum(
        cast(and_(JoinedUsers.created_time > PAST_24H, JoinedUsers.invited_by_id != null()), Integer)
    )
    result = (await session.exec(select(joins_24h, invited_joins_24h))).first()

    counts = zero_coerced(result, 2, query="participation")

    metrics.emit(MEETING_JOINS_24H_METRIC, counts[0], MetricUnit.COUNT)
    metrics.emit(INVITED_JOINS_24H_METRIC, counts[1], MetricUnit.COUNT)

    waiting_list_size = (
        await session.exec(
            select(func.count())
            .select_from(JoinedUsers)
            .join(Meetup, col(JoinedUsers.meetup_id) == col(Meetup.id))
            .where(and_(JoinedUsers.is_waiting_list == true(), Meetup.active == true()))
        )
    ).first() or 0
    metrics.emit(MetricKey.WAITING_LIST_SIZE, waiting_list_size, MetricUnit.COUNT)

    return {
        "meeting_joins_24h": counts[0],
        "invited_joins_24h": counts[1],
        "waiting_list_size": waiting_list_size,
    }


async def supporter_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    tier_counts = dict(
        (await session.exec(select(User.supporter_level, func.count()).group_by(User.supporter_level))).all()
    )
    # Every paying tier gets a gauge each run, zeros included, so the per-tier series are gap-free
    # and a future SupporterLevel member is picked up without touching this code.
    for level in SupporterLevel:
        if level is SupporterLevel.NONE:
            continue
        metrics.emit(f"{SUPPORTERS_METRIC_PREFIX}/{level.value}", tier_counts.get(level, 0), MetricUnit.COUNT)

    linked_accounts = func.count()
    in_grace = func.sum(cast(SupporterSubscription.expiration_notified, Integer))
    result = (await session.exec(select(linked_accounts, in_grace).select_from(SupporterSubscription))).first()

    counts = zero_coerced(result, 2, query="supporters")

    metrics.emit(MetricKey.LINKED_PATREON_ACCOUNTS, counts[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.SUBSCRIPTIONS_IN_GRACE, counts[1], MetricUnit.COUNT)

    return {
        "supporters_by_level": {level.value: tier_counts.get(level, 0) for level in SupporterLevel},
        "linked_patreon_accounts": counts[0],
        "subscriptions_in_grace": counts[1],
    }


async def settings_stats(session: AsyncSession, metrics: MetricsClient) -> dict[str, Any]:
    notifications_disabled = (
        await session.exec(select(func.count()).select_from(Settings).where(col(Settings.notification) == false()))
    ).first() or 0
    metrics.emit(MetricKey.NOTIFICATIONS_DISABLED, notifications_disabled, MetricUnit.COUNT)

    return {"notifications_disabled": notifications_disabled}


@with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    collected: dict[str, Any] = {}
    for collect_stats in (
        users_stats,
        new_members_stats,
        meetings_stats,
        meeting_activity_stats,
        participation_stats,
        supporter_stats,
        settings_stats,
    ):
        collected |= await collect_stats(session, metrics)

    # Assembled from what the collectors return rather than from a list kept here, so a gauge added
    # to any of them reaches the log without this function being touched — and so an all-zeros run
    # is visible in the run's own narrative instead of only in CloudWatch.
    log.info("Stats generated", **collected)
