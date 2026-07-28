import datetime as dt
import logging

import pytest
from sqlalchemy.dialects import postgresql
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.events import user_cleanup
from mitup_bot.events.user_cleanup import (
    DELETION_REQUESTED_USERS_SELECT_STATEMENT,
    DEMOTABLE_LEFT_USERS_SELECT_STATEMENT,
    INACTIVE_USERS_SELECT_STATEMENT,
)
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView
from tests.helpers import MockApi, MockDbSession, create_joined_link, create_meetup, create_member, create_user
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


def demoted_count(caplog: pytest.LogCaptureFixture) -> int:
    """The count field of the demotion log line. Absent when nothing was demoted — the pass logs
    only when it acted, so a missing line reads as zero."""
    record = next((record for record in caplog.records if record.message == "LEFT users demoted to JOINED_ONLY"), None)
    return record.__dict__["count"] if record is not None else 0


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

    # Five exec calls: demotion select + LEFT select + DELETION_REQUESTED select + delete users +
    # pending-link sweep. No meetup delete (nobody is demoted) and no per-user pending-link delete
    # either: with nobody purged there are no claimers to name.
    assert mock_session.exec.call_count == 5

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
    mock_session.add_objects_with_statement(
        INACTIVE_USERS_SELECT_STATEMENT,
        ((inactive_1.id, inactive_1.tg_user_id), (inactive_2.id, inactive_2.tg_user_id)),
    )

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

    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ((inactive.id, inactive.tg_user_id),))
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


async def test_select_retains_users_tied_to_active_meetings(mock_session: MockDbSession, api: MockApi):
    """A LEFT user is nominated only when no active meeting depends on them: two NOT EXISTS guards,
    one over owned meetups and one over joined links, both scoped to active meetings."""
    client = make_test_metrics_client()

    await user_cleanup.run(api, client)

    compiled = mock_session.normalize_query(
        str(
            INACTIVE_USERS_SELECT_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert compiled in mock_session.queries_executed
    # Owned-meeting guard.
    assert (
        "NOT (EXISTS (SELECT 1 FROM meetups WHERE meetups.owner_id = users.id AND meetups.active = true))" in compiled
    )
    # Joined-link guard.
    assert "joined_users.user_id = users.id AND meetups.active = true" in compiled


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


async def test_stale_left_user_is_demoted_to_joined_only(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    """The demotion keeps the row and drops the account: JOINED_ONLY, no stamp, and no message —
    the user blocked the bot, so there is nobody to tell."""
    caplog.set_level(logging.INFO)
    demotable = create_user(
        id=40, tg_user_id=40, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    mock_session.add_objects_with_statement(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT, (demotable,))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert demotable.status is UserStatus.JOINED_ONLY
    assert demotable.left_time is None
    api.assert_method_just_called("send_message_to_user", times=0)
    metrics.assert_emitted(name=MetricKey.LEFT_USERS_DEMOTED, value=1, unit=MetricUnit.COUNT)
    assert demoted_count(caplog) == 1


async def test_demotion_deletes_owned_inactive_meetings_and_keeps_join_links(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The owned meetings are what the demotion erases (all inactive by now, and their own data
    cascades with them); the join links are the slots the demotion exists to preserve."""
    demotable = create_user(
        id=41,
        tg_user_id=41,
        status=UserStatus.LEFT,
        left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        owned_meetings=[create_meetup(id=50, active=False)],
    )
    link = create_joined_link(demotable, create_meetup(id=51))
    mock_session.add_objects_with_statement(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT, (demotable,))

    await user_cleanup.run(api, metrics_client)

    assert (
        "DELETE FROM meetups WHERE meetups.owner_id IN (41) AND meetups.active = false" in mock_session.queries_executed
    )
    assert demotable.joined_links == [link]
    assert not any("DELETE FROM joined_users" in query for query in mock_session.queries_executed)


async def test_demoted_user_is_not_purged(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """Demotion and purge are exclusive: a demoted user holds a link to an active meeting, which is
    exactly what keeps them out of the purge select."""
    demotable = create_user(
        id=42, tg_user_id=42, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    mock_session.add_objects_with_statement(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT, (demotable,))

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed
    metrics.assert_emitted(name=MetricKey.INACTIVE_USERS_DELETED, value=0, unit=MetricUnit.COUNT)


async def test_no_demotion_candidates_touches_no_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """The zero count is emitted every run, but an empty pass issues no meeting delete."""
    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert not any("DELETE FROM meetups" in query for query in mock_session.queries_executed)
    metrics.assert_emitted(name=MetricKey.LEFT_USERS_DEMOTED, value=0, unit=MetricUnit.COUNT)


async def test_demotion_select_filters_correctly(mock_session: MockDbSession, api: MockApi):
    """The four conditions that make a LEFT user demotable: the status, the elapsed grace period,
    no active owned meeting, and at least one join link to an active meeting."""
    client = make_test_metrics_client()

    await user_cleanup.run(api, client)

    compiled = mock_session.normalize_query(
        str(
            DEMOTABLE_LEFT_USERS_SELECT_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert compiled in mock_session.queries_executed
    assert "users.status = 'left'" in compiled
    assert "users.left_time + CAST('30 days' AS INTERVAL) < now()" in compiled
    assert (
        "NOT (EXISTS (SELECT 1 FROM meetups WHERE meetups.owner_id = users.id AND meetups.active = true))" in compiled
    )
    assert (
        "(EXISTS (SELECT 1 FROM joined_users JOIN meetups ON meetups.id = joined_users.meetup_id "
        "WHERE joined_users.user_id = users.id AND meetups.active = true))" in compiled
    )


async def test_finished_pending_links_are_swept_every_run(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
):
    # patreon_pending_links has no foreign key to users, so nothing cascades into it. An unclaimed
    # row belongs to nobody the database can name, which makes this sweep the only thing that ever
    # erases it -- retention, not housekeeping.
    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    swept = next(query for query in mock_session.queries_executed if "DELETE FROM patreon_pending_links" in query)
    assert "consumed_time IS NOT NULL" in swept
    assert "expiration <= now()" in swept
    metrics.assert_emitted(name=MetricKey.PATREON_PENDING_LINKS_DELETED, value=0, unit=MetricUnit.COUNT)


async def test_deletion_request_also_erases_that_users_pending_links(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    user_with_settings: User,
):
    # A claimed row pairs a Telegram id with a Patreon id, so a data-deletion request has to reach
    # it. Nothing cascades, so the purge names it explicitly.
    user_with_settings.status = UserStatus.DELETION_REQUESTED
    mock_session.add_objects_with_statement(
        user_cleanup.DELETION_REQUESTED_USERS_SELECT_STATEMENT, (user_with_settings,)
    )

    await user_cleanup.run(api, metrics_client)

    purge = next(
        query
        for query in mock_session.queries_executed
        if "DELETE FROM patreon_pending_links" in query and "claimed_tg_user_id" in query
    )
    assert str(user_with_settings.tg_user_id) in purge


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


async def test_demotion_records_each_user_and_the_meetings_it_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The demotion deletes the user's inactive meetings before dropping the account, so the pass
    needs a per-user record. It goes ahead of the flip, which clears the stamp that says which
    grace period ran out."""
    demotable = create_user(
        id=40, tg_user_id=40, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    mock_session.add_objects_with_statement(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT, (demotable,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    record = next(entry for entry in logs if entry["event"] == "LEFT user demoted")
    assert (record["user_id"], record["tg_user_id"]) == (40, 40)
    assert record["left_time"] == dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert record["grace_days"] == user_cleanup.LEFT_STATUS_GRACE_DAYS
    assert record["reason"] == "left_grace_expired_join_link_only"

    summary = next(entry for entry in logs if entry["event"] == "LEFT users demoted to JOINED_ONLY")
    assert (summary["count"], summary["user_ids"]) == (1, [40])
    assert "meetups_deleted" in summary


async def test_both_purge_populations_record_who_was_erased(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A hard delete with only a count behind it is a GDPR-shaped erasure with no audit trail. The
    two dispositions share the event name and differ by reason, and the cascade is counted while the
    rows still exist because `cascade_delete=True` destroys them without naming them."""
    inactive = create_user(id=10, tg_user_id=100, status=UserStatus.LEFT)
    marked = create_member(id=30, tg_user_id=300, status=UserStatus.DELETION_REQUESTED)
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ((inactive.id, inactive.tg_user_id),))
    mock_session.add_objects_with_statement(DELETION_REQUESTED_USERS_SELECT_STATEMENT, (marked,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    purges = {entry["user_id"]: entry for entry in logs if entry["event"] == "User purged"}
    assert purges[10]["tg_user_id"] == 100
    assert purges[10]["reason"] == "left_and_no_active_meeting_or_join"
    assert purges[30]["tg_user_id"] == 300
    assert purges[30]["reason"] == "deletion_requested"

    farewell = next(entry for entry in logs if entry["event"] == "Deletion farewell queued")
    assert farewell["tg_user_id"] == 300

    cascade = next(entry for entry in logs if entry["event"] == "User purge cascade")
    assert cascade["user_ids"] == [10, 30]
    assert {"meetups_deleted", "join_links_deleted"} <= cascade.keys()

    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert (summary["inactive_purged"], summary["deletion_requested_purged"]) == (1, 1)
