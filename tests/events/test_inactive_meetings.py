import datetime as dt

import pytest
from sqlalchemy.dialects import postgresql
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.events import inactive_meetings
from mitup_bot.events.inactive_meetings import (
    MEETINGS_TO_DEACTIVATE_STATEMENT,
    DeactivationReason,
    SkipReason,
)
from mitup_bot.events.service import EventType
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricsClient
from mitup_bot.supporter import SupporterLevel
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


def register_due_meetings(
    mock_session: MockDbSession,
    *meetings: Meetup,
    still_due: bool = True,
    reason: DeactivationReason = DeactivationReason.PAST_END_DATETIME_PLUS_OWNER_TIMEOUT,
    level: SupporterLevel = SupporterLevel.NONE,
):
    """Register a meeting for every read the sweep performs: the unlocked nomination projection,
    the locked ``by_id`` re-load, and the under-lock eligibility re-check (empty when
    ``still_due`` is False — the meeting was rescheduled between sweep and lock).

    The nomination rows carry what the database projects: the id, the owner's Telegram id and
    tier, and which arm of the disjunction matched."""
    mock_session.add_objects_with_statement(
        inactive_meetings.DUE_MEETING_FACTS_STATEMENT,
        tuple((meeting.id, meeting.owner.tg_user_id, level.value, reason.value) for meeting in meetings),
    )
    for meeting in meetings:
        mock_session.add_object(meeting)
        mock_session.add_objects_with_statement(
            inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT.where(Meetup.id == meeting.id),
            (meeting,) if still_due else (),
        )


async def test_no_meetings_to_deactivate(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
    register_due_meetings(mock_session)
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("update_meeting_messages", times=0)


async def test_single_meeting_deactivated(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
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


async def test_meeting_no_longer_due_is_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
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


async def test_meeting_with_invited_users(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
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


async def test_meeting_membership_is_cleared(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
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


async def test_api_failure_raises_runtime_error(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
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

    # The failure counter is a number, not a report: the per-meeting id and error are on the
    # `Failed to deactivate meeting` log line, so nothing rides the record.


async def test_multiple_meetings_deactivated(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
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
# JOINED_ONLY cleanup count — reported even when no JOINED_ONLY users exist
# ---------------------------------------------------------------------------


async def test_joined_only_users_deleted_reported_when_no_orphans(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The sweep summary carries the count of JOINED_ONLY users deleted this run, zero included.

    A run that deleted nobody has to be distinguishable from a run that never got that far, so the
    field is unconditional rather than emitted only when there was something to delete.
    """
    register_due_meetings(mock_session)

    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    summary = next(entry for entry in logs if entry["event"] == "Deactivation sweep complete")
    assert summary["joined_only_users_deleted"] == 0


# ---------------------------------------------------------------------------
# JOINED_ONLY cleanup — DB integration tests live in
# tests/data/db_behavior/test_joined_only_cleanup.py because the cleanup
# query relies on `NOT EXISTS … active=true` semantics that the mock session
# cannot replay. The row-lock race tests for the per-meeting critical section
# live in tests/data/db_behavior/test_inactive_meetings_row_locks.py.
# Keep this file mock-only.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


async def test_deactivation_names_its_tier_window_and_what_it_destroyed(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The one line that acknowledges a roster existed. After the deletes nothing can reconstruct
    who was in the meeting, so the branch that nominated it, the owner's tier and window, and the
    invited users about to be hard-deleted all have to be on it."""
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside User")
    create_joined_link(user=invited, meetup=meeting, id=2)

    register_due_meetings(
        mock_session, meeting, reason=DeactivationReason.DATELESS_WINDOW_ELAPSED, level=SupporterLevel.HOST_2
    )
    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    record = next(entry for entry in logs if entry["event"] == "Deactivate meeting")
    assert record["meeting_id"] == 1
    assert record["owner_tg_user_id"] == 10
    assert record["supporter_level"] == SupporterLevel.HOST_2.value
    assert record["window_days"] == LifecyclePolicy.interval_days(
        LifecyclePolicy.get(SupporterLevel.HOST_2).dateless_lifetime
    )
    assert record["reason"] == DeactivationReason.DATELESS_WINDOW_ELAPSED.value
    assert record["invited_user_ids"] == [3]
    assert {"participants_removed", "messages_deleted", "invited_users_deleted"} <= record.keys()


async def test_skip_names_the_owner_upgrade_that_stopped_the_window(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """An owner who upgrades between nomination and lock moves to a longer window and stops being
    due — a real outcome that a bare "no longer due" sentence cannot be told apart from a reschedule."""
    meeting = create_meetup(id=1, title="Upgraded Owner Meeting")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1),
        supporter_level=SupporterLevel.HOST_2,
    )

    register_due_meetings(
        mock_session,
        meeting,
        still_due=False,
        reason=DeactivationReason.DATELESS_WINDOW_ELAPSED,
        level=SupporterLevel.NONE,
    )
    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    record = next(entry for entry in logs if entry["event"] == "Skip meeting deactivation")
    assert record["meeting_id"] == 1
    assert record["reason"] == SkipReason.TIER_WINDOW_EXTENDED.value
    assert record["nominated_reason"] == DeactivationReason.DATELESS_WINDOW_ELAPSED.value


async def test_joined_only_purge_names_the_accounts_it_took(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A bulk hard delete of real user rows. The ids are selected first because nothing downstream
    can name an account that is already gone, and a rowcount alone names nobody."""
    register_due_meetings(mock_session)
    mock_session.add_objects_with_statement(inactive_meetings.JOINED_ONLY_WITHOUT_ACTIVE_LINKS_STATEMENT, (41, 77))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    record = next(entry for entry in logs if entry["event"] == "Delete joined-only users without active links")
    assert (record["user_ids"], record["count"]) == ([41, 77], 2)
    assert "DELETE FROM users WHERE users.id IN (41, 77)" in mock_session.queries_executed


async def test_sweep_summary_reports_the_run_by_reason_and_window(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The flat counters cannot express "the Patron 365-day window suddenly fired on hundreds of
    meetings", which is the shape the October backfill cohort will arrive in."""
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_due_meetings(
        mock_session, meeting, reason=DeactivationReason.DATELESS_WINDOW_ELAPSED, level=SupporterLevel.HOST_3
    )
    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    summary = next(entry for entry in logs if entry["event"] == "Deactivation sweep complete")
    patron_window = LifecyclePolicy.interval_days(LifecyclePolicy.get(SupporterLevel.HOST_3).dateless_lifetime)
    assert (summary["nominated"], summary["deactivated"], summary["skipped"], summary["failed"]) == (1, 1, 0, 0)
    assert summary["reasons"] == {DeactivationReason.DATELESS_WINDOW_ELAPSED.value: 1}
    assert summary["windows"] == {patron_window: 1}


async def test_destruction_count_is_split_by_owner_tier(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """`deactivated=400` cannot answer whether a free cohort's cliff or a mistakenly aged-out Patron
    cohort produced it unless the sweep summary splits it by tier."""
    meeting = create_meetup(id=1, title="Patron Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_due_meetings(mock_session, meeting, level=SupporterLevel.HOST_3)
    with capture_logs(processors=[merge_contextvars]) as logs:
        await inactive_meetings.run(api, metrics_client)

    summary = next(entry for entry in logs if entry["event"] == "Deactivation sweep complete")
    assert summary["supporter_levels"] == {SupporterLevel.HOST_3.value: 1}
