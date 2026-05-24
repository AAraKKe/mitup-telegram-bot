from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from mitup_bot.cli import user_cleanup
from mitup_bot.cli.user_cleanup import INACTIVE_USERS_SELECT_STATEMENT
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
    user_cleanup.run(api, metrics_client)
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

    inactive_1 = create_user(id=10, tg_user_id=10, is_active=False)
    inactive_2 = create_user(id=11, tg_user_id=11, is_active=False)

    # Register select result — returns user IDs
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, (inactive_1.id, inactive_2.id))  # ty: ignore[invalid-argument-type]  # https://github.com/astral-sh/ty/issues/2839

    user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Two exec calls: select + delete
    assert mock_session.exec.call_count == 2

    # DELETE targets the correct user IDs (IDs 10 and 11; small integers iterate in ascending order in CPython sets)
    assert "DELETE FROM users WHERE users.id IN (10, 11)" in mock_session.queries_executed

    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=2, unit=MetricUnit.COUNT)


def test_select_query_filters_correctly(mock_session: MockDbSession):
    """Verify the SQL query selects inactive users excluding invited ones (tg_user_id != -1)."""
    api = MagicMock()
    client = make_test_metrics_client()

    user_cleanup.run(api, client)

    expected_query = mock_session.normalize_query(
        str(
            INACTIVE_USERS_SELECT_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert expected_query in mock_session.queries_executed
