import pytest
from sqlmodel import Integer, and_, cast, func, select
from sqlmodel.sql.expression import Select

from mitup_bot.cli import generate_stats
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
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
    return select(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1333
        member_users, left_users, joined_only_users, deletion_requested_users, total_users, invited_users
    )


async def test_users_stats_buckets_every_status(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """Every user lands in a bucket — MEMBER, LEFT, JOINED_ONLY, DELETION_REQUESTED, invited —
    so the per-status gauges stay consistent with the total."""
    mock_session.add_objects_with_statement(users_stats_statement(), ((5, 2, 3, 1, 12, 1),))

    await generate_stats.users_stats(mock_session, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.ACTIVE_USERS, value=5, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS, value=2, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.JOINED_ONLY_USERS, value=3, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.DELETION_REQUESTED_USERS, value=1, unit=MetricUnit.COUNT)
    metrics.assert_emitted(name=MetricKey.INVITED_USERS, value=1, unit=MetricUnit.COUNT)
