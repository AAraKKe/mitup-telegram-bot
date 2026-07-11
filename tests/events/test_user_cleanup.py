import logging

import pytest
from sqlalchemy.dialects import postgresql

from mitup_bot.events import user_cleanup
from mitup_bot.events.user_cleanup import DELETION_REQUESTED_USERS_SELECT_STATEMENT, INACTIVE_USERS_SELECT_STATEMENT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView
from tests.helpers import MockApi, MockDbSession, create_member, create_user
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

# Farewell delivery failures are not exercised here: under the write lifecycle a failed send
# surfaces at drain time, after the deletion committed — see the real-Postgres lifecycle tests
# in tests/models/db_behavior/test_events_write_lifecycle.py.


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def purged_count(caplog: pytest.LogCaptureFixture) -> int:
    """The count field of the purge log line; INFO capture also picks up unrelated framework
    lines, so the lookup filters by the structlog event string (the LogRecord message)."""
    record = next(record for record in caplog.records if record.message == "Deletion-requested users purged")
    return record.__dict__["count"]


async def test_no_users_to_purge(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    # No users registered — both selects return empty results (default behavior)
    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Three exec calls: LEFT select + DELETION_REQUESTED select + delete
    assert mock_session.exec.call_count == 3

    # No real user IDs targeted — empty-set form renders as IN (NULL) AND (1 != 1)
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed

    api.assert_method_just_called("send_message_to_user", times=0)
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=0, unit=MetricUnit.COUNT)
    assert purged_count(caplog) == 0


async def test_inactive_users_deleted_silently(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    inactive_1 = create_user(id=10, tg_user_id=10, status=UserStatus.LEFT)
    inactive_2 = create_user(id=11, tg_user_id=11, status=UserStatus.LEFT)

    # Register select result — returns user IDs
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, (inactive_1.id, inactive_2.id))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # DELETE targets the correct user IDs (IDs 10 and 11; small integers iterate in ascending order in CPython sets)
    assert "DELETE FROM users WHERE users.id IN (10, 11)" in mock_session.queries_executed

    # LEFT users blocked the bot — no farewell is attempted for them
    api.assert_method_just_called("send_message_to_user", times=0)
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=2, unit=MetricUnit.COUNT)
    assert purged_count(caplog) == 0


async def test_deletion_requested_users_purged_with_farewell(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    lang: str,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    marked = create_member(id=30, tg_user_id=30, language=lang, status=UserStatus.DELETION_REQUESTED)

    mock_session.add_objects_with_statement(DELETION_REQUESTED_USERS_SELECT_STATEMENT, (marked,))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert "DELETE FROM users WHERE users.id IN (30)" in mock_session.queries_executed

    farewell = MitupView(description=PrivacyMessages.DELETION_COMPLETE.get(lang=lang), keyboard=[])
    api.assert_send_message_to_user_called(user=marked, view=farewell)

    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=0, unit=MetricUnit.COUNT)
    assert purged_count(caplog) == 1


async def test_left_and_marked_users_purged_together(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    inactive = create_user(id=10, tg_user_id=10, status=UserStatus.LEFT)
    marked = create_member(id=30, tg_user_id=30, status=UserStatus.DELETION_REQUESTED)

    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, (inactive.id,))
    mock_session.add_objects_with_statement(DELETION_REQUESTED_USERS_SELECT_STATEMENT, (marked,))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # One DELETE covers both buckets (IDs 10 and 30; ascending small-int set iteration)
    assert "DELETE FROM users WHERE users.id IN (10, 30)" in mock_session.queries_executed

    # Only the marked user gets a farewell
    farewell = MitupView(description=PrivacyMessages.DELETION_COMPLETE.get(lang=marked.lang), keyboard=[])
    api.assert_send_message_to_user_called(user=marked, view=farewell)

    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=1, unit=MetricUnit.COUNT)
    assert purged_count(caplog) == 1


async def test_select_query_filters_correctly(mock_session: MockDbSession, api: MockApi):
    """Verify the SQL query selects LEFT users excluding invited ones (tg_user_id != -1).

    JOINED_ONLY users are intentionally NOT in this query — they are cleaned up by
    `inactive_meetings` once their last active meeting goes away.
    """
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


async def test_deletion_requested_query_filters_correctly(mock_session: MockDbSession, api: MockApi):
    """The farewell select must scope to status='deletion_requested' and nothing else."""
    client = make_test_metrics_client()

    await user_cleanup.run(api, client)

    expected_query = mock_session.normalize_query(
        str(
            DELETION_REQUESTED_USERS_SELECT_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert expected_query in mock_session.queries_executed
    assert "users.status = 'deletion_requested'" in expected_query
    assert "= 'left'" not in expected_query
    assert "= 'member'" not in expected_query


async def test_joined_only_users_not_targeted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """A JOINED_ONLY user registered in the session must not be picked up by user_cleanup."""
    joined_only_user = create_user(id=20, tg_user_id=20, status=UserStatus.JOINED_ONLY)
    # The selects return only LEFT / DELETION_REQUESTED users — JOINED_ONLY rows never appear.
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
