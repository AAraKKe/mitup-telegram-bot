import datetime as dt
from unittest.mock import ANY

import pytest
from sqlalchemy.dialects import postgresql

from mitup_bot.events import inactive_meetings
from mitup_bot.events.inactive_meetings import MEETINGS_TO_DEACTIVATE_STATEMENT
from mitup_bot.events.service import EventType
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def register_due_meetings(mock_session: MockDbSession, *meetings: Meetup, still_due: bool = True):
    """Register a meeting for every read the sweep performs: the unlocked candidate sweep,
    the locked ``by_id`` re-load, and the under-lock eligibility re-check (empty when
    ``still_due`` is False — the meeting was rescheduled between sweep and lock)."""
    mock_session.add_objects_with_statement(inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, meetings)
    for meeting in meetings:
        mock_session.add_object(meeting)
        mock_session.add_objects_with_statement(
            inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT.where(Meetup.id == meeting.id),
            (meeting,) if still_due else (),
        )


async def test_no_meetings_to_deactivate(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    register_due_meetings(mock_session)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("update_meeting_messages", times=0)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_single_meeting_deactivated(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_due_meetings(mock_session, meeting)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is False
    assert meeting.expiration_time is not None
    assert isinstance(meeting.expiration_time, dt.datetime)

    # Verify update_meeting_messages was called with has_finished=True for this meeting.
    # No session is passed: the call runs under write-mode capture and executes post-commit.
    call_kwargs = api.mock_method("update_meeting_messages").call_args.kwargs
    assert call_kwargs["has_finished"] is True
    assert call_kwargs["meeting"] is meeting
    assert "session" not in call_kwargs

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_meeting_no_longer_due_is_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """The under-lock re-check found the meeting rescheduled: it is neither deactivated nor
    counted as failed — the next sweep re-evaluates it from scratch."""
    meeting = create_meetup(id=1, title="Rescheduled Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_due_meetings(mock_session, meeting, still_due=False)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is True
    assert meeting.expiration_time is None
    api.assert_method_just_called("update_meeting_messages", times=0)

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_meeting_with_invited_users(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    # Regular user (tg_user_id != -1) should NOT be deleted
    regular_user = create_user(id=2, tg_user_id=200)
    create_joined_link(user=regular_user, meetup=meeting, id=1)

    # Invited (outside) user (tg_user_id == -1) should be deleted
    invited_user = create_user(id=3, tg_user_id=-1, first_name="Outside User")
    create_joined_link(user=invited_user, meetup=meeting, id=2)

    register_due_meetings(mock_session, meeting)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is False

    # Only the invited user (id=3) should be targeted by the DELETE; the regular user (id=2) must not appear.
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed
    assert f"DELETE FROM joined_users WHERE joined_users.meetup_id = {meeting.id}" in mock_session.queries_executed
    assert f"DELETE FROM messages WHERE messages.meetup_id = {meeting.id}" in mock_session.queries_executed

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_meeting_membership_is_cleared(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """Deactivation empties the meeting: participants and waiting-list entries alike.

    Both live in `joined_users`, so one delete scoped to the meeting covers them. What the meeting
    reads back afterwards — including through a reactivation — is pinned in
    tests/data/db_behavior/test_meeting_deactivation_cleanup.py.
    """
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    participant = create_user(id=2, tg_user_id=200)
    create_joined_link(user=participant, meetup=meeting, id=1)

    waiting = create_user(id=3, tg_user_id=300)
    create_joined_link(user=waiting, meetup=meeting, id=2, is_waiting_list=True)

    register_due_meetings(mock_session, meeting)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is False
    assert f"DELETE FROM joined_users WHERE joined_users.meetup_id = {meeting.id}" in mock_session.queries_executed
    # The participants are real users, only their membership goes: nothing deletes users by id
    # (the JOINED_ONLY sweep at the end of the run deletes by status, not by id).
    assert not any("DELETE FROM users WHERE users.id IN" in query for query in mock_session.queries_executed)

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_api_failure_raises_runtime_error(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_fail = create_meetup(id=2, title="Fail Meeting")
    create_user(id=2, tg_user_id=20, owned_meetings=[meeting_fail], settings=create_settings(id=2))

    register_due_meetings(mock_session, meeting_ok, meeting_fail)

    # First call succeeds (returns None), second call raises
    api.mock_method("update_meeting_messages").side_effect = [None, RuntimeError("API timeout")]

    with pytest.raises(RuntimeError, match="Failed to deactivate 1 meetings"):
        await inactive_meetings.run(api, metrics_client)

    await metrics_client.flush()

    # First meeting committed in its own transaction, so the second meeting's failure
    # cannot roll it back
    assert meeting_ok.active is False
    assert meeting_ok.expiration_time is not None

    # Second meeting should remain active since its processing failed
    assert meeting_fail.active is True

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
        properties={"failed_details": ANY},
    )


async def test_multiple_meetings_deactivated(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting_a = create_meetup(id=1, title="Meeting A")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_a], settings=create_settings(id=1))

    meeting_b = create_meetup(id=2, title="Meeting B")
    create_user(id=2, tg_user_id=20, owned_meetings=[meeting_b], settings=create_settings(id=2))

    register_due_meetings(mock_session, meeting_a, meeting_b)

    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting_a.active is False
    assert meeting_b.active is False
    assert meeting_a.expiration_time is not None
    assert meeting_b.expiration_time is not None

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


def test_deactivation_statement_generates_a_branch_per_tier_window():
    """The dateless-meeting predicate carries one branch per distinct tier window plus the LEFT-owner
    branch, all rendered from the lifecycle policy against `activated_time`. Which meetings each
    window selects is covered in tests/data/db_behavior/test_lifecycle_windows.py."""
    compiled = " ".join(
        str(
            MEETINGS_TO_DEACTIVATE_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )

    for duration, levels in LifecyclePolicy.levels_by_duration(lambda policy: policy.dateless_lifetime).items():
        rendered_levels = ", ".join(f"'{level.value}'" for level in levels)
        assert (
            f"users.supporter_level IN ({rendered_levels}) AND meetups.activated_time "
            f"+ CAST('{LifecyclePolicy.interval_days(duration)} days' AS INTERVAL) < now()" in compiled
        )

    left_days = LifecyclePolicy.interval_days(LifecyclePolicy.get().left_owner_dateless_lifetime)
    assert (
        "meetups.datetime IS NULL AND users.status = 'left' "
        f"AND meetups.activated_time + CAST('{left_days} days' AS INTERVAL) < now()" in compiled
    )


# ---------------------------------------------------------------------------
# JOINED_ONLY cleanup metric — fires even when no JOINED_ONLY users exist
# ---------------------------------------------------------------------------


async def test_joined_only_users_deleted_metric_emitted_when_no_orphans(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """`JOINED_ONLY_USERS_DELETED` is always emitted, even with no candidates to delete.

    The metric is a counter, not a conditional emission — it gets the count of users
    deleted in this run, including zero. Operators rely on the gauge being present.
    """
    register_due_meetings(mock_session)

    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(
        name=MetricKey.JOINED_ONLY_USERS_DELETED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


# ---------------------------------------------------------------------------
# JOINED_ONLY cleanup — DB integration tests live in
# tests/data/db_behavior/test_joined_only_cleanup.py because the cleanup
# query relies on `NOT EXISTS … active=true` semantics that the mock session
# cannot replay. The row-lock race tests for the per-meeting critical section
# live in tests/data/db_behavior/test_inactive_meetings_row_locks.py.
# Keep this file mock-only.
# ---------------------------------------------------------------------------
