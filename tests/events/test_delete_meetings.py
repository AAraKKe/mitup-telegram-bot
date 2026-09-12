import datetime as dt
import logging
import re
from collections.abc import Callable

import pytest
from sqlalchemy.dialects import postgresql
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram.error import BadRequest

from mitup_bot.events import delete_meetings
from mitup_bot.events.deletion_sweep import STUCK_RESIDUE_DAYS, ResidueReason
from mitup_bot.events.service import EventType
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.supporter import SupporterLevel
from mitup_bot.views import meeting as meeting_views
from tests.events.deletion_sweep_helpers import (
    fail_transaction,
    over_one_chunk,
    owned_meetups,
    register_nominated,
    residue_record,
    transaction_count,
)
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

DELETED_MEETUP_IDS_PATTERN = re.compile(r"DELETE FROM meetups WHERE meetups\.id IN \(([^)]*)\)")

# The owners built here carry no supporter level, so the residue ages are measured against the free
# policy's windows, derived rather than pinned so a retuned duration keeps the intended overdue days.
RETENTION_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().inactive_retention)
LEAD_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().deletion_warning_lead)

STATEMENT = delete_meetings.MEETUPS_TO_DELETE_STATEMENT
SWEEP_DIMENSIONS = {"EventType": EventType.DELETE_MEETINGS.value}


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions=SWEEP_DIMENSIONS)


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def deleted_meetup_ids(session: MockDbSession) -> set[int]:
    """The meetup ids the run's DELETE removed, read back from the SQL it executed."""
    ids: set[int] = set()
    for query in session.queries_executed:
        match = DELETED_MEETUP_IDS_PATTERN.search(query)
        if match:
            ids.update(int(part) for part in match.group(1).split(",") if part.strip().isdigit())
    return ids


async def test_no_meetings_to_delete(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    register_nominated(mock_session, STATEMENT, ())
    await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_emitted(name=MetricKey.MEETUPS_DELETED_UNNOTIFIED, value=0, dimensions=SWEEP_DIMENSIONS)
    metrics.assert_emitted(name=MetricKey.MEETINGS_DELETION_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_delete_meeting_successfully(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="To Delete", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    register_nominated(mock_session, STATEMENT, (meeting,))

    await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    # MockApi does not override send_messages_to_users, so the real TelegramApi implementation
    # runs. It iterates over users and calls self.send_message_to_user(...) per user, which
    # MockApi does override and routes through call_mock to an AsyncMock. The assertion therefore
    # lands on the real mock and is meaningful.
    api.assert_send_message_to_user_called(user=owner, view=meeting_views.deletion_notice_view([meeting]))

    # The meeting was notified, so it is part of the DELETE.
    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # No outside users linked to this meeting; SQLAlchemy renders an empty IN as IN (NULL) AND (1 != 1).
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed

    metrics.assert_emitted(name=MetricKey.MEETUPS_DELETED_UNNOTIFIED, value=0, dimensions=SWEEP_DIMENSIONS)
    metrics.assert_emitted(name=MetricKey.MEETINGS_DELETION_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_delete_meeting_with_outside_users(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="To Delete")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    # An invited (outside) user linked to this meeting
    outside_user = create_user(id=2, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=outside_user, meetup=meeting, id=1)

    register_nominated(mock_session, STATEMENT, (meeting,))

    await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # The outside user (id=2) must be deleted; the owner (id=1, tg_user_id != -1) must not appear.
    assert "DELETE FROM users WHERE users.id IN (2)" in mock_session.queries_executed

    metrics.assert_emitted(name=MetricKey.MEETINGS_DELETION_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_delete_meeting_whose_owner_is_unreachable(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_unnotified = create_meetup(id=2, title="Blocked Owner Meeting")
    owner_blocked = create_user(
        id=2, tg_user_id=20, owned_meetings=[meeting_unnotified], settings=create_settings(id=2)
    )
    # Naive UTC, the shape the expiration column reads back with.
    meeting_unnotified.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(
        days=RETENTION_DAYS + 2
    )

    # An invited user of the unnotified meeting must be purged with it, exactly as for a notified one.
    outside_user = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=outside_user, meetup=meeting_unnotified, id=1)

    register_nominated(mock_session, STATEMENT, (meeting_ok, meeting_unnotified))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert owner_blocked.status is UserStatus.LEFT
    # The owner blocked the bot, so the notice can never land: the meeting is deleted regardless.
    assert deleted_meetup_ids(mock_session) == {1, 2}
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed

    record = residue_record(caplog, "Meeting deleted without notifying its owner")
    assert record.__dict__["reason"] == ResidueReason.OWNER_UNREACHABLE.value
    assert record.__dict__["meeting_id"] == 2
    assert record.__dict__["days_overdue"] == 2

    metrics.assert_emitted(name=MetricKey.MEETUPS_DELETED_UNNOTIFIED, value=1, dimensions=SWEEP_DIMENSIONS)
    # An owner who cannot be reached is not a failure to delete.
    metrics.assert_emitted(name=MetricKey.MEETINGS_DELETION_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_delete_meeting_whose_owner_is_unreachable_is_not_nominated_again(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await delete_meetings.run(api, metrics_client)
    sends_after_first_run = api.mock_method("send_message_to_user").call_count

    # The next day's run only sees the rows the first run left behind.
    surviving: tuple[Meetup, ...] = tuple(
        candidate for candidate in (meeting,) if candidate.id not in deleted_meetup_ids(mock_session)
    )
    mock_session.statements_registry.clear()
    register_nominated(mock_session, STATEMENT, surviving)

    await delete_meetings.run(api, metrics_client)

    assert surviving == ()
    assert api.mock_method("send_message_to_user").call_count == sends_after_first_run


async def test_delete_is_deferred_when_the_notice_raises(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_failed = create_meetup(id=2, title="Unlucky Meeting")
    owner_failed = create_user(id=2, tg_user_id=20, owned_meetings=[meeting_failed], settings=create_settings(id=2))
    meeting_failed.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS + 3)

    register_nominated(mock_session, STATEMENT, (meeting_ok, meeting_failed))
    api.mock_method("send_message_to_user").side_effect = [None, BadRequest("Bad Request: chat is unavailable")]

    await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    # A transient Telegram failure is not evidence the owner is gone: the meeting stays.
    assert owner_failed.status is UserStatus.MEMBER
    assert deleted_meetup_ids(mock_session) == {1}

    record = residue_record(caplog, "Meeting deletion deferred")
    assert record.__dict__["reason"] == ResidueReason.OWNER_NOTIFICATION_FAILED.value
    assert record.__dict__["meeting_id"] == 2
    assert record.__dict__["days_overdue"] == 3

    metrics.assert_emitted(name=MetricKey.MEETUPS_DELETED_UNNOTIFIED, value=0, dimensions=SWEEP_DIMENSIONS)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=1,
        properties={"failed_meeting_ids": [2]},
        dimensions=SWEEP_DIMENSIONS,
    )


@pytest.mark.parametrize(
    ("duration_of", "column"),
    [
        (lambda policy: policy.inactive_retention, "expiration_time"),
        (lambda policy: policy.deletion_warning_lead, "warned_time"),
    ],
    ids=["retention", "lead"],
)
def test_the_deletion_statement_carries_a_branch_per_tier_window(
    duration_of: Callable[[LifecyclePolicy], dt.timedelta], column: str
):
    """The deletion carries two windows, the retention from `expiration_time` and the lead from
    `warned_time`, because a warning the sweep issued late has to carry its own deadline with it.
    Which meetings each one selects is covered in tests/data/db_behavior/test_lifecycle_windows.py."""
    compiled = " ".join(
        str(STATEMENT.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).split()
    )

    for duration, levels in LifecyclePolicy.levels_by_duration(duration_of).items():
        rendered_levels = ", ".join(f"'{level.value}'" for level in levels)
        assert (
            f"users.supporter_level IN ({rendered_levels}) AND meetups.{column} "
            f"+ CAST('{LifecyclePolicy.interval_days(duration)} days' AS INTERVAL) < now()" in compiled
        )


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


async def test_a_meeting_waiting_out_its_lead_is_not_counted_as_overdue(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, caplog: pytest.LogCaptureFixture
):
    """A late warning moves the deletion with it, so the days a meeting spends waiting out its lead
    are not days it is running late. Measured against the retention alone this meeting would be
    reported as months overdue and would trip the stuck-residue escalation on a healthy run."""
    caplog.set_level(logging.WARNING)
    meeting = create_meetup(id=1, title="Late Warning Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    # Naive UTC, the shape the timestamp columns read back with.
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=RETENTION_DAYS + 30)
    meeting.warned_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=LEAD_DAYS + 1)

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await delete_meetings.run(api, metrics_client)

    record = residue_record(caplog, "Meeting deleted without notifying its owner")
    assert record.__dict__["days_overdue"] == 1


async def test_a_repeatedly_failing_send_escalates_instead_of_warning_daily_forever(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A send that raised defers the meeting to the next run, which re-nominates and re-warns it at
    the same level forever. Past the threshold the retry is not working, so the row gets an error of
    its own: that is what makes the failure counter mean "failing now" rather than "ever failed"."""
    meeting = create_meetup(id=1, title="Stuck Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(
        days=RETENTION_DAYS + STUCK_RESIDUE_DAYS + 2
    )

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Telegram is having a moment")

    with capture_logs(processors=[merge_contextvars]) as logs:
        await delete_meetings.run(api, metrics_client)

    stuck = next(entry for entry in logs if entry["event"] == "Meeting deletion stuck")
    assert stuck["log_level"] == "error"
    assert stuck["meeting_id"] == 1
    assert stuck["days_overdue"] == STUCK_RESIDUE_DAYS + 2
    assert stuck["reason"] == "owner_notification_failed_repeatedly"
    assert not [entry for entry in logs if entry["event"] == "Meeting deletion deferred"]


async def test_purge_names_the_meetings_and_the_invitees_it_destroys(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A purge has to leave behind something the warnings that preceded it can be reconciled
    against: the meeting ids, the owner tiers, and the invitee rows that cascade with them."""
    meeting = create_meetup(id=1, title="Purged Meeting")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1),
        supporter_level=SupporterLevel.HOST_2,
    )
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=meeting, id=1)

    register_nominated(mock_session, STATEMENT, (meeting,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await delete_meetings.run(api, metrics_client)

    purged = next(entry for entry in logs if entry["event"] == "Meetups purged")
    assert (purged["count"], purged["meeting_ids"], purged["unnotified"]) == (1, [1], 0)
    assert purged["supporter_levels"] == {"none": 0, "host_1": 0, "host_2": 1, "host_3": 0}
    assert purged["reason"] == "retention_elapsed"

    invitees = next(entry for entry in logs if entry["event"] == "Invitee users purged")
    assert (invitees["count"], invitees["user_ids"]) == (1, [3])

    summary = next(entry for entry in logs if entry["event"] == "Meetup purge sweep complete")
    assert (summary["purged"], summary["invitee_users_purged"]) == (1, 1)


async def test_an_owner_who_turned_the_notice_off_is_not_written_to_but_the_meeting_is_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """The notice is the only thing the toggle silences: the meeting is purged in the same run, and
    the deletion is not counted as one its owner was never told about."""
    meeting = create_meetup(id=1, title="Quietly Deleted")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1, deletion_notice=False),
    )

    register_nominated(mock_session, STATEMENT, (meeting,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=0)
    assert deleted_meetup_ids(mock_session) == {1}

    purged = next(entry for entry in logs if entry["event"] == "Meetups purged")
    assert (purged["count"], purged["opted_out"], purged["unnotified"]) == (1, 1, 0)

    metrics.assert_emitted(name=MetricKey.MEETUPS_DELETED_UNNOTIFIED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_the_purge_writes_only_to_the_owners_who_still_want_the_notice(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    silent = create_meetup(id=1, title="Opted Out")
    create_user(id=1, tg_user_id=10, owned_meetings=[silent], settings=create_settings(id=1, deletion_notice=False))
    told = create_meetup(id=2, title="Still Notified")
    owner = create_user(id=2, tg_user_id=20, owned_meetings=[told], settings=create_settings(id=2))

    register_nominated(mock_session, STATEMENT, (silent, told))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await delete_meetings.run(api, metrics_client)

    api.assert_send_message_to_user_called(user=owner, view=meeting_views.deletion_notice_view([told]))
    api.assert_method_just_called("send_message_to_user", times=1)
    assert deleted_meetup_ids(mock_session) == {1, 2}

    summary = next(entry for entry in logs if entry["event"] == "Meetup purge sweep complete")
    assert (summary["nominated"], summary["delivered"], summary["opted_out"], summary["purged"]) == (2, 1, 1, 2)


# ---------------------------------------------------------------------------
# One digest per owner
# ---------------------------------------------------------------------------


async def test_an_owner_whose_meetings_are_deleted_is_told_once(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meetings = owned_meetups(1, 3, first_meeting_id=1)
    register_nominated(mock_session, STATEMENT, tuple(meetings))

    await delete_meetings.run(api, metrics_client)

    api.assert_send_message_to_user_called(user=meetings[0].owner, view=meeting_views.deletion_notice_view(meetings))
    assert deleted_meetup_ids(mock_session) == {1, 2, 3}


async def test_a_failed_notice_digest_defers_every_meeting_it_named(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meetings = owned_meetups(1, 3, first_meeting_id=1)
    register_nominated(mock_session, STATEMENT, tuple(meetings))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Bad Request: chat is unavailable")

    await delete_meetings.run(api, metrics_client)

    assert deleted_meetup_ids(mock_session) == set()


async def test_the_purge_lines_count_owners_beside_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A run's volume is owners messaged, not meetings nominated: with the digest the two numbers
    part company, and only the first one says how many messages went out."""
    first = owned_meetups(1, 3, first_meeting_id=1)
    second = owned_meetups(2, 1, first_meeting_id=4)
    register_nominated(mock_session, STATEMENT, (*first, *second))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await delete_meetings.run(api, metrics_client)

    purged = next(entry for entry in logs if entry["event"] == "Meetups purged")
    assert (purged["count"], purged["owners"]) == (4, 2)

    summary = next(entry for entry in logs if entry["event"] == "Meetup purge sweep complete")
    assert (summary["delivered"], summary["owners_messaged"]) == (4, 2)


# ---------------------------------------------------------------------------
# One transaction per chunk of owners
# ---------------------------------------------------------------------------


async def test_the_purge_commits_one_transaction_per_chunk_of_owners(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The sweep commits as it goes rather than once at the end: the nomination read, then one
    transaction per chunk of owners. Every nominated owner is still written to exactly once."""
    meetings = over_one_chunk()
    chunks = register_nominated(mock_session, STATEMENT, meetings)

    await delete_meetings.run(api, metrics_client)

    assert len(chunks) == 3
    api.assert_method_just_called("send_message_to_user", times=len(meetings))
    assert transaction_count(mock_session) == 1 + len(chunks)


async def test_a_failed_chunk_leaves_the_meetings_of_the_chunks_around_it_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """A chunk that cannot open its transaction costs its own owners and nothing else. The sweep
    still reports the counters it owes for what it did commit, then raises so the lost chunk is the
    run's fault and the next daily run re-nominates its owners."""
    meetings = over_one_chunk()
    chunks = register_nominated(mock_session, STATEMENT, meetings)
    # The nomination read is the first transaction, so the second chunk is the third.
    fail_transaction(mock_session, 3)

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(RuntimeError, match="cleanup chunks"):
            await delete_meetings.run(api, metrics_client)
    await metrics_client.flush()

    lost_owners = chunks[1]
    surviving = {meeting.id for meeting in meetings if meeting.owner.db_id in lost_owners}
    assert deleted_meetup_ids(mock_session) & surviving == set()
    assert len(deleted_meetup_ids(mock_session)) == len(meetings) - len(lost_owners)

    summary = next(entry for entry in logs if entry["event"] == "Meetup purge sweep complete")
    assert (summary["chunks"], summary["failed_chunks"]) == (len(chunks), 1)

    metrics.assert_emitted(name=MetricKey.MEETINGS_DELETION_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)
