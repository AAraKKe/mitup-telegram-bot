import datetime as dt
import logging

import pytest
from sqlalchemy.dialects import postgresql
from sqlmodel import and_, col, delete, false, or_, select
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.events import user_cleanup
from mitup_bot.events.user_cleanup import (
    DELETION_REQUESTED_USER_IDS_STATEMENT,
    DELETION_REQUESTED_USERS_SELECT_STATEMENT,
    DEMOTABLE_LEFT_USERS_SELECT_STATEMENT,
    INACTIVE_USERS_SELECT_STATEMENT,
    MEETINGS_JOINED_BY_DELETION_REQUESTED_USERS_STATEMENT,
    PURGEABLE_LEFT_USER,
    LeaveSkipReason,
    leave_skip_reason,
)
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_joined_link,
    create_meetup,
    create_member,
    create_settings,
    create_user,
)
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


def record_rewrites(api: MockApi, mock_session: MockDbSession, delete_prefix: str) -> list[tuple[int, bool]]:
    """Record each deleted-card rewrite as its meeting id and whether a DELETE starting with
    *delete_prefix* had already run when it was queued."""
    rewrites: list[tuple[int, bool]] = []

    def record(*, meeting: Meetup, was_deleted: bool):
        assert was_deleted
        rewrites.append(
            (meeting.db_id, any(query.startswith(delete_prefix) for query in mock_session.queries_executed))
        )

    api.register_on_method("update_meeting_messages", side_effect=record)
    return rewrites


def register_marked(mock_session: MockDbSession, *users: User) -> set[int]:
    """Register deletion-requested users for the run's two reads of them: the snapshot of ids taken
    before the leave step, and the full rows restricted to that snapshot. Returns the snapshot."""
    snapshot = {user.db_id for user in users}
    mock_session.add_objects_with_statement(DELETION_REQUESTED_USER_IDS_STATEMENT, tuple(snapshot))
    mock_session.add_objects_with_statement(
        DELETION_REQUESTED_USERS_SELECT_STATEMENT.where(col(User.id).in_(snapshot)), users
    )
    return snapshot


def register_purge(
    mock_session: MockDbSession, left_ids: set[int], marked_ids: set[int], deleted: set[int] | None = None
) -> str:
    """Register the purge DELETE to hand back *deleted* (every selected id by default) and return
    the SQL it runs as, so a test can assert it ran with the status re-checks in place."""
    statement = (
        delete(User)
        .where(
            or_(
                and_(col(User.id).in_(left_ids), PURGEABLE_LEFT_USER),
                and_(col(User.id).in_(marked_ids), User.status == UserStatus.DELETION_REQUESTED),
            )
        )
        .returning(col(User.id))
    )
    returned = left_ids | marked_ids if deleted is None else deleted
    mock_session.add_objects_with_statement(statement, tuple((user_id,) for user_id in sorted(returned)))
    return mock_session.normalize_query(
        str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    )


def register_demotable(mock_session: MockDbSession, *candidates: User, still_demotable: tuple[User, ...] | None = None):
    """Register the demotion's two reads: the candidates, and the locked re-check of them, which
    hands back *still_demotable* (every candidate by default)."""
    mock_session.add_objects_with_statement(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT, candidates)
    mock_session.add_objects_with_statement(
        DEMOTABLE_LEFT_USERS_SELECT_STATEMENT.where(col(User.id).in_({user.db_id for user in candidates})),
        candidates if still_demotable is None else still_demotable,
    )


def demoted_count(caplog: pytest.LogCaptureFixture) -> int:
    """The count field of the demotion log line. Absent when nothing was demoted — the pass logs
    only when it acted, so a missing line reads as zero."""
    record = next((record for record in caplog.records if record.message == "LEFT users demoted to JOINED_ONLY"), None)
    return record.__dict__["count"] if record is not None else 0


async def test_no_users_to_purge(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    purge = register_purge(mock_session, set(), set())
    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Nine exec calls: demotion select + deletion-request snapshot + LEFT select + DELETION_REQUESTED
    # select + the two cascade counts + the select of the cascaded meetings with cards + delete users
    # + pending-link sweep. With nobody to erase there is no leave step, no meetup delete and no
    # per-user pending-link delete.
    assert mock_session.exec.call_count == 9
    assert purge in mock_session.queries_executed

    api.assert_method_just_called("send_message_to_user", times=0)
    assert purged_count(caplog) == 0


async def test_inactive_users_deleted_silently(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
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

    purge = register_purge(mock_session, {10, 11}, set())

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert purge in mock_session.queries_executed

    # LEFT users blocked the bot — no farewell is attempted for them
    api.assert_method_just_called("send_message_to_user", times=0)
    assert purged_count(caplog) == 0


async def test_deletion_requested_users_purged_with_farewell(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    lang: str,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    marked = create_member(id=30, tg_user_id=30, language=lang, status=UserStatus.DELETION_REQUESTED)
    register_marked(mock_session, marked)
    purge = register_purge(mock_session, set(), {30})

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert purge in mock_session.queries_executed

    farewell = MitupView(message=PrivacyMessages.DELETION_COMPLETE.rich(lang=lang), menu=[])
    api.assert_send_message_to_user_called(user=marked, view=farewell)

    assert purged_count(caplog) == 1


async def test_left_and_marked_users_purged_together(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    inactive = create_user(id=10, tg_user_id=10, status=UserStatus.LEFT)
    marked = create_member(id=30, tg_user_id=30, status=UserStatus.DELETION_REQUESTED)

    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ((inactive.id, inactive.tg_user_id),))
    register_marked(mock_session, marked)
    purge = register_purge(mock_session, {10}, {30})

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # One DELETE covers both populations.
    assert purge in mock_session.queries_executed

    # Only the marked user gets a farewell
    farewell = MitupView(message=PrivacyMessages.DELETION_COMPLETE.rich(lang=marked.lang), menu=[])
    api.assert_send_message_to_user_called(user=marked, view=farewell)

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
    """The snapshot of who a run erases must scope to status='deletion_requested' and nothing else."""
    client = make_test_metrics_client()

    await user_cleanup.run(api, client)

    expected_query = mock_session.normalize_query(
        str(
            DELETION_REQUESTED_USER_IDS_STATEMENT.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    )
    assert expected_query in mock_session.queries_executed
    assert "users.status = 'deletion_requested'" in expected_query
    assert "= 'left'" not in expected_query
    assert "= 'member'" not in expected_query


async def test_joined_only_users_not_targeted(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
    """A JOINED_ONLY user registered in the session must not be picked up by user_cleanup."""
    joined_only_user = create_user(id=20, tg_user_id=20, status=UserStatus.JOINED_ONLY)
    # The selects return only LEFT / DELETION_REQUESTED users — JOINED_ONLY rows never appear.
    # Registering an empty result for the statement keeps the test honest: even if a
    # JOINED_ONLY row existed, the select would not return it.
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ())
    purge = register_purge(mock_session, set(), set())

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    # Empty ids leave the DELETE with nothing to match; the JOINED_ONLY user is never targeted.
    assert purge in mock_session.queries_executed
    # User stays JOINED_ONLY — no side effects.
    assert joined_only_user.status is UserStatus.JOINED_ONLY


async def test_stale_left_user_is_demoted_to_joined_only(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    """The demotion keeps the row and drops the account: JOINED_ONLY, no stamp, and no message —
    the user blocked the bot, so there is nobody to tell."""
    caplog.set_level(logging.INFO)
    demotable = create_user(
        id=40, tg_user_id=40, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    register_demotable(mock_session, demotable)

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert demotable.status is UserStatus.JOINED_ONLY
    assert demotable.left_time is None
    api.assert_method_just_called("send_message_to_user", times=0)
    assert demoted_count(caplog) == 1


async def test_a_candidate_who_stopped_qualifying_under_the_lock_is_not_demoted(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The locked re-check is what decides: a user it no longer returns keeps their status and their
    meetings, and the pass says it let them go."""
    demotable = create_user(
        id=43,
        tg_user_id=43,
        status=UserStatus.LEFT,
        left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        owned_meetings=[create_meetup(id=52, active=False)],
    )
    register_demotable(mock_session, demotable, still_demotable=())

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    assert demotable.status is UserStatus.LEFT
    assert not any("DELETE FROM meetups" in query for query in mock_session.queries_executed)
    skipped = next(entry for entry in logs if entry["event"] == "LEFT user demotion skipped")
    assert (skipped["user_id"], skipped["reason"]) == (43, "no_longer_demotable")
    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert summary["demoted"] == 0


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
    register_demotable(mock_session, demotable)

    await user_cleanup.run(api, metrics_client)

    assert (
        "DELETE FROM meetups WHERE meetups.owner_id IN (41) AND meetups.active = false" in mock_session.queries_executed
    )
    assert demotable.joined_links == [link]
    assert not any("DELETE FROM joined_users" in query for query in mock_session.queries_executed)


async def test_demotion_rewrites_the_cards_of_the_meetings_it_deletes_before_deleting_them(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The meetings with a tracked card are loaded by the same condition the DELETE uses, so the
    banner goes to exactly the cards the DELETE takes away."""
    carded = create_meetup(id=50, active=False)
    demotable = create_user(
        id=41,
        tg_user_id=41,
        status=UserStatus.LEFT,
        left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        owned_meetings=[carded, create_meetup(id=51, active=False)],
    )
    register_demotable(mock_session, demotable)
    owned_inactive = and_(col(Meetup.owner_id).in_([41]), Meetup.active == false())
    mock_session.add_objects_with_statement(
        select(Meetup).where(owned_inactive, user_cleanup.HAS_TRACKED_CARD), (carded,)
    )
    rewrites = record_rewrites(api, mock_session, "DELETE FROM meetups")

    await user_cleanup.run(api, metrics_client)

    assert rewrites == [(50, False)]


async def test_user_purge_rewrites_the_cards_of_the_meetings_of_the_users_it_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The meetings go with their owners through the cascade, so the cards are rewritten only for
    the owners the DELETE actually took, from meetings loaded while the rows still existed."""
    finished = create_meetup(id=60, active=False)
    inactive = create_user(id=10, tg_user_id=10, status=UserStatus.LEFT, owned_meetings=[finished])
    live = create_meetup(id=61, active=True)
    marked = create_user(
        id=30,
        tg_user_id=30,
        status=UserStatus.DELETION_REQUESTED,
        settings=create_settings(id=30),
        owned_meetings=[live],
    )
    mock_session.add_objects_with_statement(INACTIVE_USERS_SELECT_STATEMENT, ((inactive.id, inactive.tg_user_id),))
    register_marked(mock_session, marked)
    mock_session.add_objects_with_statement(
        select(Meetup).where(col(Meetup.owner_id).in_({10, 30}), user_cleanup.HAS_TRACKED_CARD), (finished, live)
    )
    # The LEFT user came back between the select and the DELETE, so the DELETE spares them.
    register_purge(mock_session, {10}, {30}, deleted={30})
    rewrites = record_rewrites(api, mock_session, "DELETE FROM users")

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    assert rewrites == [(61, True)]
    spared = next(entry for entry in logs if entry["event"] == "User purge skipped")
    assert (spared["user_id"], spared["reason"]) == (10, "no_longer_purgeable")
    assert [entry["user_id"] for entry in logs if entry["event"] == "User purged"] == [30]


async def test_demoted_user_is_not_purged(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi):
    """Demotion and purge are exclusive: a demoted user holds a link to an active meeting, which is
    exactly what keeps them out of the purge select."""
    demotable = create_user(
        id=42, tg_user_id=42, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    register_demotable(mock_session, demotable)
    purge = register_purge(mock_session, set(), set())

    await user_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    assert purge in mock_session.queries_executed


async def test_no_demotion_candidates_touches_no_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The zero count is reported every run, but an empty pass issues no meeting delete."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    assert not any("DELETE FROM meetups" in query for query in mock_session.queries_executed)
    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert summary["demoted"] == 0


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
    api: MockApi,
):
    # patreon_pending_links has no foreign key to users, so nothing cascades into it. An unclaimed
    # row belongs to nobody the database can name, which makes this sweep the only thing that ever
    # erases it -- retention, not housekeeping.
    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    swept = next(query for query in mock_session.queries_executed if "DELETE FROM patreon_pending_links" in query)
    assert "consumed_time IS NOT NULL" in swept
    assert "expiration <= now()" in swept
    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert summary["pending_links_purged"] == 0


async def test_deletion_request_also_erases_that_users_pending_links(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    user_with_settings: User,
):
    # A claimed row pairs a Telegram id with a Patreon id, so a data-deletion request has to reach
    # it. Nothing cascades, so the purge names it explicitly.
    user_with_settings.status = UserStatus.DELETION_REQUESTED
    register_purge(mock_session, set(), register_marked(mock_session, user_with_settings))

    await user_cleanup.run(api, metrics_client)

    purge = next(
        query
        for query in mock_session.queries_executed
        if "DELETE FROM patreon_pending_links" in query and "claimed_tg_user_id" in query
    )
    assert str(user_with_settings.tg_user_id) in purge


# ---------------------------------------------------------------------------
# Meetings a deletion-requested user joined
# ---------------------------------------------------------------------------


def full_meeting_left_by_a_deletion_request(
    mock_session: MockDbSession, *, owner_status: UserStatus = UserStatus.MEMBER
) -> tuple[Meetup, User, User]:
    """A full meeting whose one place is held by a deletion-requested user, with somebody waiting,
    registered for the purge's reads. Returns the meeting, the leaving user and the waiting one."""
    meeting = create_meetup(id=70, max_members=1, waiting_list=True)
    create_user(id=80, tg_user_id=80, status=owner_status, settings=create_settings(id=80), owned_meetings=[meeting])
    leaving = create_user(id=31, tg_user_id=31, status=UserStatus.DELETION_REQUESTED, settings=create_settings(id=31))
    waiter = create_user(id=32, tg_user_id=32, settings=create_settings(id=32))
    create_joined_link(leaving, meeting, id=1, created_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    create_joined_link(waiter, meeting, id=2, created_time=dt.datetime(2026, 1, 2, tzinfo=dt.UTC), is_waiting_list=True)
    marked = (leaving,) if owner_status is UserStatus.MEMBER else (leaving, meeting.owner)
    snapshot = register_marked(mock_session, *marked)
    mock_session.add_objects_with_statement(
        MEETINGS_JOINED_BY_DELETION_REQUESTED_USERS_STATEMENT.where(col(User.id).in_(snapshot)), (70,)
    )
    mock_session.add_object(meeting)
    register_purge(mock_session, set(), snapshot)
    return meeting, leaving, waiter


async def test_a_deletion_requested_participant_leaves_a_joined_meeting_before_the_purge(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The place they held goes to the next one waiting, who is told, and the cards are redrawn
    from the meeting without them, all before the user row is deleted."""
    meeting, leaving, waiter = full_meeting_left_by_a_deletion_request(mock_session)
    refreshes: list[tuple[list[int | None], bool]] = []

    def record(*, meeting: Meetup):
        refreshes.append(
            (
                [link.user_id for link in meeting.joined_links if not link.is_waiting_list],
                any(query.startswith("DELETE FROM users") for query in mock_session.queries_executed),
            )
        )

    api.register_on_method("update_meeting_messages", side_effect=record)

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    assert refreshes == [([32], False)]
    assert [call.kwargs["user"] for call in api.call_args_list("send_message_to_user")] == [waiter, leaving]
    left = next(entry for entry in logs if entry["event"] == "Meeting leave resolved")
    assert (left["leaving_user_id"], left["meeting_id"], left["reason"]) == (31, 70, "account_deletion_requested")
    promotion = next(entry for entry in logs if entry["event"] == "Waiting list promoted")
    assert (promotion["promoted_user_ids"], promotion["reason"]) == ([32], "participant_account_deleted")
    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert (summary["joined_links_removed"], summary["failed_meetings"]) == (1, 0)


async def test_a_meeting_that_fails_to_commit_is_not_counted_as_left(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The links only went once the transaction committed, so a failed commit counts as a failure alone."""
    full_meeting_left_by_a_deletion_request(mock_session)
    # The demotion, the snapshot and the nomination close first; the fourth is the meeting's own.
    commits = mock_session.begin.return_value.__aexit__
    commits.side_effect = [None, None, None, RuntimeError("commit lost"), *([None] * 10)]

    with capture_logs(processors=[merge_contextvars]) as logs, pytest.raises(RuntimeError, match="1 meetings"):
        await user_cleanup.run(api, metrics_client)

    summary = next(entry for entry in logs if entry["event"] == "User cleanup complete")
    assert (summary["joined_links_removed"], summary["failed_meetings"]) == (0, 1)


@pytest.mark.parametrize(
    "state, expected",
    [
        ("missing", LeaveSkipReason.MEETING_DELETED),
        ("inactive", LeaveSkipReason.ALREADY_INACTIVE),
        ("owner_leaving", LeaveSkipReason.OWNER_DELETION_REQUESTED),
        ("nobody_leaving", LeaveSkipReason.NO_DELETION_REQUESTED_PARTICIPANT),
        ("somebody_leaving", None),
    ],
)
def test_the_locked_meeting_names_why_it_keeps_every_link(state: str, expected: LeaveSkipReason | None):
    meeting = create_meetup(id=70, active=state != "inactive")
    create_user(id=80, tg_user_id=80, owned_meetings=[meeting])
    leaving = create_user(id=31, tg_user_id=31, status=UserStatus.DELETION_REQUESTED)
    link = create_joined_link(leaving, meeting, id=1)
    leaving_user_ids = {31, 80} if state == "owner_leaving" else {31}

    reason = leave_skip_reason(
        None if state == "missing" else meeting,
        [] if state == "nobody_leaving" else [link],
        leaving_user_ids,
    )

    assert reason is expected


async def test_a_meeting_whose_owner_also_asked_for_deletion_is_left_to_the_purge(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The purge deletes that meeting with its owner, so nobody is promoted into it first."""
    meeting, _leaving, waiter = full_meeting_left_by_a_deletion_request(
        mock_session, owner_status=UserStatus.DELETION_REQUESTED
    )

    with capture_logs(processors=[merge_contextvars]) as logs:
        await user_cleanup.run(api, metrics_client)

    assert len(meeting.joined_links) == 2
    assert waiter not in [call.kwargs["user"] for call in api.call_args_list("send_message_to_user")]
    api.assert_update_meeting_messages_not_called()
    skipped = next(entry for entry in logs if entry["event"] == "Deleted account meeting leave skipped")
    assert skipped["reason"] == "owner_deletion_requested"


async def test_a_meeting_that_fails_to_release_its_place_does_not_hold_back_the_purge(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The erasure goes ahead and the run reports the failure, so the alarm still fires."""
    full_meeting_left_by_a_deletion_request(mock_session)
    api.register_on_method("update_meeting_messages", side_effect=RuntimeError("card cannot render"))

    with capture_logs(processors=[merge_contextvars]) as logs, pytest.raises(RuntimeError, match="1 meetings"):
        await user_cleanup.run(api, metrics_client)

    assert [entry["user_id"] for entry in logs if entry["event"] == "User purged"] == [31]
    failure = next(entry for entry in logs if entry["event"] == "Failed to remove a deleted account from a meeting")
    assert (failure["log_level"], failure["meeting_id"], failure["reason"]) == ("error", 70, "leave_failed")


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
    register_demotable(mock_session, demotable)

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
    register_marked(mock_session, marked)
    register_purge(mock_session, {10}, {30})

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
