import pytest
from sqlmodel import Integer, and_, cast, col, false, func, null, or_, select, true
from sqlmodel.sql.expression import Select, SelectOfScalar

from mitup_bot.events import generate_stats
from mitup_bot.models import JoinedUsers, Meetup, Settings, SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import SUPPORTED_LANGUAGES
from tests.helpers import MockDbSession
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def users_stats_statement() -> Select:
    """Rebuild the aggregate select `users_stats` issues, so its result can be registered on
    the mock session (identical construction compiles to the identical registry key)."""
    member_users = func.sum(cast(User.status == UserStatus.MEMBER, Integer))
    left_users = func.sum(cast(User.status == UserStatus.LEFT, Integer))
    joined_only_users = func.sum(cast(and_(User.status == UserStatus.JOINED_ONLY, User.tg_user_id != -1), Integer))
    deletion_requested_users = func.sum(cast(User.status == UserStatus.DELETION_REQUESTED, Integer))
    total_users = func.count()
    invited_users = func.sum(cast(User.tg_user_id == -1, Integer))
    new_users_24h = func.sum(cast(User.created_time > generate_stats.PAST_24H, Integer))
    return select(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1333
        member_users,
        left_users,
        joined_only_users,
        deletion_requested_users,
        total_users,
        invited_users,
        new_users_24h,
    )


def language_stats_statement() -> Select:
    """Rebuild the per-language MEMBER count select `users_stats` issues."""
    return (
        select(Settings.language, func.count())
        .join(User, col(Settings.user_id) == col(User.id))
        .where(User.status == UserStatus.MEMBER)
        .group_by(Settings.language)
    )


def meetings_stats_statement() -> Select:
    """Rebuild the aggregate select `meetings_stats` issues, so its result can be registered on
    the mock session (identical construction compiles to the identical registry key)."""
    active_meetings = func.sum(cast(Meetup.active, Integer))
    total_meetings = func.count()
    incognito_meetings = func.sum(cast(and_(Meetup.active, Meetup.incognito), Integer))
    public_meetings = func.sum(cast(and_(Meetup.active, Meetup.public), Integer))
    meetings_with_invitation = func.sum(cast(and_(Meetup.active, Meetup.allow_invitation), Integer))
    meetings_with_datetime = func.sum(cast(and_(Meetup.active, Meetup.datetime != null()), Integer))
    return select(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1333
        active_meetings,
        total_meetings,
        incognito_meetings,
        public_meetings,
        meetings_with_invitation,
        meetings_with_datetime,
    )


def meeting_activity_statement() -> Select:
    """Rebuild the aggregate select `meeting_activity_stats` issues."""
    created_24h = func.sum(cast(Meetup.created_time > generate_stats.PAST_24H, Integer))
    finished_24h = func.sum(cast(Meetup.expiration_time > generate_stats.PAST_24H, Integer))
    upcoming_24h = func.sum(
        cast(and_(Meetup.active, Meetup.datetime > func.now(), Meetup.datetime <= generate_stats.NEXT_24H), Integer)
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
    return select(created_24h, finished_24h, upcoming_24h, in_progress)


def participation_statement() -> Select:
    """Rebuild the joined-users aggregate select `participation_stats` issues."""
    joins_24h = func.sum(cast(JoinedUsers.created_time > generate_stats.PAST_24H, Integer))
    invited_joins_24h = func.sum(
        cast(and_(JoinedUsers.created_time > generate_stats.PAST_24H, JoinedUsers.invited_by_id != null()), Integer)
    )
    return select(joins_24h, invited_joins_24h)


def waiting_list_statement() -> SelectOfScalar[int]:
    """Rebuild the active-meeting waiting-list count select `participation_stats` issues."""
    return (
        select(func.count())
        .select_from(JoinedUsers)
        .join(Meetup, col(JoinedUsers.meetup_id) == col(Meetup.id))
        .where(and_(JoinedUsers.is_waiting_list == true(), Meetup.active == true()))
    )


def supporter_tiers_statement() -> Select:
    """Rebuild the per-tier user count select `supporter_stats` issues."""
    return select(User.supporter_level, func.count()).group_by(User.supporter_level)


def subscriptions_statement() -> Select:
    """Rebuild the subscriptions aggregate select `supporter_stats` issues."""
    linked_accounts = func.count()
    in_grace = func.sum(cast(SupporterSubscription.expiration_notified, Integer))
    return select(linked_accounts, in_grace).select_from(SupporterSubscription)


def notifications_disabled_statement() -> SelectOfScalar[int]:
    """Rebuild the notifications-off count select `settings_stats` issues."""
    return select(func.count()).select_from(Settings).where(col(Settings.notification) == false())


async def test_users_stats_buckets_every_status(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """Every user lands in a bucket — MEMBER, LEFT, JOINED_ONLY, DELETION_REQUESTED, invited —
    so the per-status gauges stay consistent with the total, and the 24h growth gauge rides along."""
    mock_session.add_objects_with_statement(users_stats_statement(), ((5, 2, 3, 1, 12, 1, 4),))

    await generate_stats.users_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_USERS, value=5, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS, value=2, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.JOINED_ONLY_USERS, value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.DELETION_REQUESTED_USERS, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INVITED_USERS, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.TOTAL_USERS, value=12, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.NEW_USERS_24H_METRIC, value=4, unit=MetricUnit.COUNT)


async def test_users_stats_emits_zeros_on_empty_table(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """An empty users table makes every SUM NULL and COUNT zero — every gauge, including the total,
    is still emitted as 0 (the truthful statistic) and no fault is raised."""
    mock_session.add_objects_with_statement(users_stats_statement(), ((None, None, None, None, 0, None, None),))

    await generate_stats.users_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.JOINED_ONLY_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.DELETION_REQUESTED_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INVITED_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.TOTAL_USERS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.NEW_USERS_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_users_stats_language_gauges_count_members_only(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """The per-language gauges come from the MEMBER-joined language query, so their sum matches
    ActiveUsers instead of counting LEFT/JOINED_ONLY/DELETION_REQUESTED settings rows."""
    mock_session.add_objects_with_statement(users_stats_statement(), ((5, 2, 3, 1, 12, 1, 0),))
    mock_session.add_objects_with_statement(language_stats_statement(), (("en", 3), ("es_ES", 2)))

    await generate_stats.users_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_USERS.with_prefix("en"), value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.ACTIVE_USERS.with_prefix("es_ES"), value=2, unit=MetricUnit.COUNT)


async def test_users_stats_language_gauges_cannot_mint_a_series_from_the_database(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """A `settings.language` value the bot does not ship — a row predating a locale change, or one
    written by a future migration — must not become a CloudWatch series of its own: a metric name is
    billed forever. It lands in the bounded `Other` bucket instead, keeping the per-language sum
    equal to ActiveUsers."""
    mock_session.add_objects_with_statement(users_stats_statement(), ((6, 0, 0, 0, 6, 0, 0),))
    mock_session.add_objects_with_statement(language_stats_statement(), (("en", 3), ("sv_SE", 2), ("klingon", 1)))

    await generate_stats.users_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_not_emitted(name=MetricKey.ACTIVE_USERS.with_prefix("sv_SE"))
    metrics.assert_not_emitted(name=MetricKey.ACTIVE_USERS.with_prefix("klingon"))
    metrics.assert_emitted(
        name=MetricKey.ACTIVE_USERS.with_prefix(generate_stats.OTHER_LANGUAGE_BUCKET), value=3, unit=MetricUnit.COUNT
    )

    emitted_names = {record.name for record in metrics_client.records if record.name.endswith("/ActiveUsers")}
    allowed_names = {
        MetricKey.ACTIVE_USERS.with_prefix(language)
        for language in [*SUPPORTED_LANGUAGES, generate_stats.OTHER_LANGUAGE_BUCKET]
    }
    assert emitted_names == allowed_names


async def test_meetings_stats_buckets_every_meeting(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """Populated meetings produce the total/active pair plus the active-only feature-adoption
    gauges; the derived inactive count is not emitted (dashboard metric math computes it)."""
    mock_session.add_objects_with_statement(meetings_stats_statement(), ((7, 10, 2, 4, 3, 6),))

    await generate_stats.meetings_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_MEETINGS, value=7, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.TOTAL_MEETINGS, value=10, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INCOGNITO_MEETINGS, value=2, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.PUBLIC_MEETINGS, value=4, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_WITH_INVITATION, value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_WITH_DATETIME, value=6, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name="InactiveMeetings")


async def test_meetings_stats_emits_zeros_on_empty_table(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """An empty meetings table makes every SUM NULL and COUNT zero — every gauge, including the
    total, is still emitted as 0 (the truthful statistic) and no fault is raised."""
    mock_session.add_objects_with_statement(meetings_stats_statement(), ((None, 0, None, None, None, None),))

    await generate_stats.meetings_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_MEETINGS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.TOTAL_MEETINGS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INCOGNITO_MEETINGS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.PUBLIC_MEETINGS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_WITH_INVITATION, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_WITH_DATETIME, value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_meeting_activity_stats_emits_all_gauges(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(meeting_activity_statement(), ((3, 2, 5, 1),))

    await generate_stats.meeting_activity_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=generate_stats.MEETINGS_CREATED_24H_METRIC, value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.MEETINGS_FINISHED_24H_METRIC, value=2, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.UPCOMING_MEETINGS_24H_METRIC, value=5, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_IN_PROGRESS, value=1, unit=MetricUnit.COUNT)


async def test_meeting_activity_stats_emits_zeros_on_empty_table(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(meeting_activity_statement(), ((None, None, None, None),))

    await generate_stats.meeting_activity_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=generate_stats.MEETINGS_CREATED_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.MEETINGS_FINISHED_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.UPCOMING_MEETINGS_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.MEETINGS_IN_PROGRESS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_participation_stats_emits_join_and_waiting_gauges(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(participation_statement(), ((9, 4),))
    mock_session.add_objects_with_statement(waiting_list_statement(), (6,))

    await generate_stats.participation_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=generate_stats.MEETING_JOINS_24H_METRIC, value=9, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.INVITED_JOINS_24H_METRIC, value=4, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.WAITING_LIST_SIZE, value=6, unit=MetricUnit.COUNT)


async def test_participation_stats_emits_zeros_on_empty_tables(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(participation_statement(), ((None, None),))

    await generate_stats.participation_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=generate_stats.MEETING_JOINS_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=generate_stats.INVITED_JOINS_24H_METRIC, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.WAITING_LIST_SIZE, value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_supporter_stats_emits_every_paying_tier_including_zeros(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """Tiers absent from the group-by still emit 0 so the per-tier series are gap-free, and NONE
    never gets a gauge."""
    mock_session.add_objects_with_statement(
        supporter_tiers_statement(), ((SupporterLevel.NONE, 40), (SupporterLevel.HOST_1, 3))
    )
    mock_session.add_objects_with_statement(subscriptions_statement(), ((5, 2),))

    await generate_stats.supporter_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name="Supporters/host_1", value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name="Supporters/host_2", value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name="Supporters/host_3", value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name="Supporters/none")
    metrics.assert_emitted(name=MetricKey.LINKED_PATREON_ACCOUNTS, value=5, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.SUBSCRIPTIONS_IN_GRACE, value=2, unit=MetricUnit.COUNT)


async def test_supporter_stats_emits_zeros_on_empty_tables(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(subscriptions_statement(), ((0, None),))

    await generate_stats.supporter_stats(mock_session, metrics_client)
    await metrics_client.flush()

    for level in (SupporterLevel.HOST_1, SupporterLevel.HOST_2, SupporterLevel.HOST_3):
        metrics.assert_emitted(name=f"Supporters/{level.value}", value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.LINKED_PATREON_ACCOUNTS, value=0, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.SUBSCRIPTIONS_IN_GRACE, value=0, unit=MetricUnit.COUNT)
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_settings_stats_emits_notifications_disabled_count(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    mock_session.add_objects_with_statement(notifications_disabled_statement(), (8,))

    await generate_stats.settings_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.NOTIFICATIONS_DISABLED, value=8, unit=MetricUnit.COUNT)
