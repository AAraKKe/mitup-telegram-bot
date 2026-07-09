import datetime as dt

import pytest

from mitup_bot.cli.broadcast import recording
from mitup_bot.cli.broadcast.types import BatchResult, DeliveryOutcome, PendingDelivery
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.cli.broadcast.helpers import batch_of, script_exec
from tests.helpers import MockDbSession, Result, create_member
from tests.helpers.monitoring import MetricAssertions

SENT = BroadcastDeliveryStatus.SENT
FAILED = BroadcastDeliveryStatus.FAILED
SKIPPED = BroadcastDeliveryStatus.SKIPPED_INACTIVE
RETRY_PENDING = BroadcastDeliveryStatus.RETRY_PENDING

DELIVERY_SENT = MetricKey.BROADCAST_DELIVERY_SENT
DELIVERY_FAILED = MetricKey.BROADCAST_DELIVERY_FAILED
DELIVERY_RETRY = MetricKey.BROADCAST_DELIVERY_RETRY_PENDING
DELIVERY_SKIPPED = MetricKey.BROADCAST_DELIVERY_SKIPPED_INACTIVE
DELIVERY_KEYS = [DELIVERY_SENT, DELIVERY_FAILED, DELIVERY_RETRY, DELIVERY_SKIPPED]


@pytest.mark.parametrize("with_sent_time", [True, False], ids=["with_sent_time", "without_sent_time"])
async def test_mark_deliveries_issues_update(mock_session: MockDbSession, with_sent_time: bool):
    script_exec(mock_session, Result())
    sent_time = dt.datetime.now(dt.UTC) if with_sent_time else None

    await recording.mark_deliveries(mock_session, [DeliveryOutcome(1, 10, SENT)], SENT, sent_time=sent_time)

    assert mock_session.exec.await_count == 1
    assert "UPDATE broadcast_deliveries" in mock_session.queries_executed[0]


async def test_deactivate_skipped_users_marks_members_left_and_returns_count(mock_session: MockDbSession):
    member = create_member(1, 20, status=UserStatus.MEMBER)
    script_exec(mock_session, Result(results=(member,)))

    left = await recording.deactivate_skipped_users(mock_session, [DeliveryOutcome(1, 1, SKIPPED)])

    assert member.status is UserStatus.LEFT
    # The count is returned for the caller to emit INACTIVE_USER_SET post-commit — not emitted here.
    assert left == 1


async def test_deactivate_skipped_users_returns_zero_when_nothing_transitions(mock_session: MockDbSession):
    already_left = create_member(1, 20, status=UserStatus.LEFT)
    script_exec(mock_session, Result(results=(already_left,)))

    left = await recording.deactivate_skipped_users(mock_session, [DeliveryOutcome(1, 1, SKIPPED)])

    assert left == 0


async def test_record_batch_outcomes_marks_sent_and_deactivates_skipped(mock_session: MockDbSession):
    skipped_member = create_member(2, 22, status=UserStatus.MEMBER)
    outcomes = [DeliveryOutcome(101, 1, SENT), DeliveryOutcome(102, 2, SKIPPED)]
    # mark(sent) update, mark(skipped) update, deactivate select
    script_exec(mock_session, Result(), Result(), Result(results=(skipped_member,)))

    deactivated = await recording.record_batch_outcomes(batch_of(*outcomes))

    assert skipped_member.status is UserStatus.LEFT
    # Returns the deactivated count so the caller emits INACTIVE_USER_SET post-commit.
    assert deactivated == 1


async def test_record_batch_outcomes_skipped_only_deactivates_without_marking_sent(mock_session: MockDbSession):
    skipped_member = create_member(2, 22, status=UserStatus.MEMBER)
    # No SENT outcomes: only the skipped mark update and the deactivate select run.
    script_exec(mock_session, Result(), Result(results=(skipped_member,)))

    deactivated = await recording.record_batch_outcomes(batch_of(DeliveryOutcome(102, 2, SKIPPED)))

    assert skipped_member.status is UserStatus.LEFT
    assert deactivated == 1


async def test_record_batch_outcomes_sent_only_touches_no_users(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    deactivated = await recording.record_batch_outcomes(batch_of(DeliveryOutcome(101, 1, SENT)))

    assert mock_session.exec.await_count == 1
    assert deactivated == 0


async def test_record_batch_outcomes_failed_only_marks_failed_without_deactivating(mock_session: MockDbSession):
    """The claim no longer pre-sets FAILED, so a FAILED outcome now needs its own explicit write."""
    script_exec(mock_session, Result())

    deactivated = await recording.record_batch_outcomes(batch_of(DeliveryOutcome(101, 1, FAILED)))

    assert mock_session.exec.await_count == 1
    assert "'failed'" in mock_session.queries_executed[0]
    assert deactivated == 0


async def test_record_batch_outcomes_schedules_retry_pending_rows(mock_session: MockDbSession):
    next_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
    script_exec(mock_session, Result())

    deactivated = await recording.record_batch_outcomes(batch_of(DeliveryOutcome(101, 1, RETRY_PENDING, next_attempt)))

    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "'retry_pending'" in query
    assert "next_attempt_time" in query
    assert deactivated == 0


async def test_schedule_retries_writes_each_row_with_its_own_next_attempt_time(mock_session: MockDbSession):
    first_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
    second_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=125)
    script_exec(mock_session, Result(), Result())

    await recording.schedule_retries(
        mock_session,
        [DeliveryOutcome(101, 1, RETRY_PENDING, first_attempt), DeliveryOutcome(102, 2, RETRY_PENDING, second_attempt)],
    )

    assert mock_session.exec.await_count == 2
    assert all("'retry_pending'" in query for query in mock_session.queries_executed)


async def test_release_unattempted_reparks_and_restores_attempt_budget(mock_session: MockDbSession):
    script_exec(mock_session, Result())
    unattempted = [
        PendingDelivery(201, create_member(1, 11, "en"), "en", 2),
        PendingDelivery(202, create_member(2, 12, "en"), "en", 2),
    ]

    await recording.release_unattempted(mock_session, unattempted, dt.timedelta(seconds=25))

    # One bulk UPDATE flips the rows back to RETRY_PENDING, sets next_attempt_time, and undoes the
    # claim's increment so their attempt budget is not spent on a non-attempt.
    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcast_deliveries" in query
    assert "'retry_pending'" in query
    assert "next_attempt_time" in query
    assert "attempt_count=(broadcast_deliveries.attempt_count - 1)" in query


async def test_record_batch_outcomes_releases_unattempted_rows_under_flood_control(
    mock_session: MockDbSession,
):
    # schedule_retries writes the flood-triggering row, then release_unattempted bulk-releases the
    # untried remainder in the same transaction.
    script_exec(mock_session, Result(), Result())
    result = BatchResult(
        outcomes=[DeliveryOutcome(101, 1, RETRY_PENDING, dt.datetime.now(dt.UTC))],
        flood_control=True,
        unattempted=[PendingDelivery(102, create_member(2, 12, "en"), "en", 1)],
        flood_backoff=dt.timedelta(seconds=25),
    )

    deactivated = await recording.record_batch_outcomes(result)

    assert mock_session.exec.await_count == 2
    release_query = mock_session.queries_executed[1]
    assert "attempt_count=(broadcast_deliveries.attempt_count - 1)" in release_query
    assert "'retry_pending'" in release_query
    # No skipped rows in this batch, so nothing was deactivated.
    assert deactivated == 0


async def test_emit_delivery_outcomes_is_one_hot_per_recorded_outcome(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    outcomes = [DeliveryOutcome(101, 1, SENT), DeliveryOutcome(102, 2, SKIPPED)]

    recording.emit_delivery_outcomes(metrics_client, outcomes, 7)
    await metrics_client.flush()

    # Each outcome sets its own key to 1 and the other three to 0. Only broadcast_id (run-constant)
    # rides as a property — no per-delivery `attempt`, which the shared EMF logger would clobber.
    metrics.assert_emitted(name=DELIVERY_SENT, value=1, properties={"broadcast_id": 7}, properties_exact=True, times=1)
    metrics.assert_emitted(name=DELIVERY_SKIPPED, value=1, properties={"broadcast_id": 7}, times=1)
    # The two statuses this batch never saw are always 0 — never 1.
    metrics.assert_not_emitted(name=DELIVERY_FAILED, value=1)
    metrics.assert_not_emitted(name=DELIVERY_RETRY, value=1)
    # Every key fires exactly once per recorded delivery — two outcomes here.
    for key in DELIVERY_KEYS:
        metrics.assert_emitted(name=key, times=2)


async def test_emit_delivery_outcomes_emits_nothing_for_an_empty_batch(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    # The flood-released remainder is never in result.outcomes, so it produces no delivery metric.
    recording.emit_delivery_outcomes(metrics_client, [], 7)
    await metrics_client.flush()

    for key in DELIVERY_KEYS:
        metrics.assert_not_emitted(name=key)


async def test_record_batch_outcomes_writes_all_three_terminal_groups_for_a_mixed_batch(mock_session: MockDbSession):
    skipped_member = create_member(3, 33, status=UserStatus.MEMBER)
    outcomes = [
        DeliveryOutcome(101, 1, SENT),
        DeliveryOutcome(102, 2, FAILED),
        DeliveryOutcome(103, 3, SKIPPED),
    ]
    # mark(sent), mark(failed), mark(skipped), then the deactivate-skipped-users select.
    script_exec(mock_session, Result(), Result(), Result(), Result(results=(skipped_member,)))

    deactivated = await recording.record_batch_outcomes(batch_of(*outcomes))

    assert mock_session.exec.await_count == 4
    queries = mock_session.queries_executed
    assert "'sent'" in queries[0]
    assert "'failed'" in queries[1]
    assert "'skipped_inactive'" in queries[2]
    assert skipped_member.status is UserStatus.LEFT
    assert deactivated == 1
