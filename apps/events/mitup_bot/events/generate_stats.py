from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Integer, and_, cast, col, distinct, false, func, literal, null, or_, select, true
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.db import with_session
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import SUPPORTED_LANGUAGES

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


async def users_stats(session: AsyncSession, metrics: MetricsClient):
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

    # An empty table sums to NULL across zero rows; the truthful statistic is 0 for every gauge.
    # The annotation pins the type: the ty-suppressed select() overload leaves `result` as Unknown.
    counts: list[int] = [value or 0 for value in result] if result is not None else [0] * 7

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


async def meetings_stats(session: AsyncSession, metrics: MetricsClient):
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

    # An empty table sums to NULL across zero rows; the truthful statistic is 0 for every gauge.
    counts: list[int] = [value or 0 for value in result] if result is not None else [0] * 6

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


async def meeting_activity_stats(session: AsyncSession, metrics: MetricsClient):
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

    # An empty table sums to NULL across zero rows; the truthful statistic is 0 for every gauge.
    counts: list[int] = [value or 0 for value in result] if result is not None else [0] * 4

    metrics.emit(MEETINGS_CREATED_24H_METRIC, counts[0], MetricUnit.COUNT)
    metrics.emit(MEETINGS_FINISHED_24H_METRIC, counts[1], MetricUnit.COUNT)
    metrics.emit(UPCOMING_MEETINGS_24H_METRIC, counts[2], MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_IN_PROGRESS, counts[3], MetricUnit.COUNT)


async def participation_stats(session: AsyncSession, metrics: MetricsClient):
    joins_24h = func.sum(cast(JoinedUsers.created_time > PAST_24H, Integer))
    invited_joins_24h = func.sum(
        cast(and_(JoinedUsers.created_time > PAST_24H, JoinedUsers.invited_by_id != null()), Integer)
    )
    result = (await session.exec(select(joins_24h, invited_joins_24h))).first()

    # An empty table sums to NULL across zero rows; the truthful statistic is 0 for every gauge.
    counts: list[int] = [value or 0 for value in result] if result is not None else [0] * 2

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


async def supporter_stats(session: AsyncSession, metrics: MetricsClient):
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

    # An empty table sums to NULL across zero rows; the truthful statistic is 0 for every gauge.
    counts: list[int] = [value or 0 for value in result] if result is not None else [0] * 2

    metrics.emit(MetricKey.LINKED_PATREON_ACCOUNTS, counts[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.SUBSCRIPTIONS_IN_GRACE, counts[1], MetricUnit.COUNT)


async def settings_stats(session: AsyncSession, metrics: MetricsClient):
    notifications_disabled = (
        await session.exec(select(func.count()).select_from(Settings).where(col(Settings.notification) == false()))
    ).first() or 0
    metrics.emit(MetricKey.NOTIFICATIONS_DISABLED, notifications_disabled, MetricUnit.COUNT)


@with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    await users_stats(session, metrics)
    await meetings_stats(session, metrics)
    await meeting_activity_stats(session, metrics)
    await participation_stats(session, metrics)
    await supporter_stats(session, metrics)
    await settings_stats(session, metrics)
