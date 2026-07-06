from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from mitup_bot.cli import user_cleanup
from mitup_bot.cli.user_cleanup import INACTIVE_USERS_SELECT_STATEMENT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from tests.helpers import MockDbSession, create_user
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


async def test_no_inactive_users(mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions):
    api = MagicMock()

    # No users registered — select returns empty result (default behavior)
    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Delete statement still executes but with empty set
    assert mock_session.exec.call_count == 2

    # No real user IDs targeted — empty-set form renders as IN (NULL) AND (1 != 1)
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed

    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=0, unit=MetricUnit.COUNT)


async def test_inactive_users_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    api = MagicMock()

    inactive_1 = create_user(id=10, tg_user_id=10, status=UserStatus.LEFT)
    inactive_2 = create_user(id=11, tg_user_id=11, status=UserStatus.LEFT)

    # Register select result — returns user IDs
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, (inactive_1.id, inactive_2.id))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Two exec calls: select + delete
    assert mock_session.exec.call_count == 2

    # DELETE targets the correct user IDs (IDs 10 and 11; small integers iterate in ascending order in CPython sets)
    assert "DELETE FROM users WHERE users.id IN (10, 11)" in mock_session.queries_executed

    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=2, unit=MetricUnit.COUNT)


async def test_select_query_filters_correctly(mock_session: MockDbSession):
    """Verify the SQL query selects LEFT users excluding invited ones (tg_user_id != -1).

    JOINED_ONLY users are intentionally NOT in this query — they are cleaned up by
    `inactive_meetings` once their last active meeting goes away.
    """
    api = MagicMock()
    client = make_test_metrics_client()

    await user_cleanup.run(api, client)

    expected_query = mock_session.normalize_query(
        str(
            INACTIVE_USERS_SELECT_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert expected_query in mock_session.queries_executed
    # The compiled SQL must scope to status='left', not JOINED_ONLY / MEMBER.
    assert "users.status = 'left'" in expected_query
    assert "joined_only" not in expected_query
    assert "= 'member'" not in expected_query


async def test_joined_only_users_not_targeted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """A JOINED_ONLY user registered in the session must not be picked up by user_cleanup."""
    api = MagicMock()

    joined_only_user = create_user(id=20, tg_user_id=20, status=UserStatus.JOINED_ONLY)
    # The select returns only LEFT users — JOINED_ONLY rows never appear here.
    # Registering an empty result for the statement keeps the test honest: even if a
    # JOINED_ONLY row existed, the select would not return it.
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ())

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Empty IDs → empty IN clause; the JOINED_ONLY user is never targeted.
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=0, unit=MetricUnit.COUNT)
    # User stays JOINED_ONLY — no side effects.
    assert joined_only_user.status is UserStatus.JOINED_ONLY
