import datetime as dt
import logging

import pytest
from sqlalchemy.dialects import postgresql
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram.error import BadRequest

from mitup_bot.events import warn_meeting_deletions
from mitup_bot.events.deletion_sweep import ResidueReason
from mitup_bot.events.service import EventType
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
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
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

# The owners built here carry no supporter level, so the residue ages are measured against the free
# policy's windows, derived rather than pinned so a retuned duration keeps the intended overdue days.
WARNING_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().deletion_warning_delay)

STATEMENT = warn_meeting_deletions.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT
SWEEP_DIMENSIONS = {"EventType": EventType.WARN_MEETING_DELETIONS.value}


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.WARN_MEETING_DELETIONS.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


async def test_no_meetings_to_warn(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    register_nominated(mock_session, STATEMENT, ())
    await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_emitted(name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_notify_meeting_about_to_be_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Expiring Meeting", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    register_nominated(mock_session, STATEMENT, (meeting,))

    await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    # MockApi does not override send_messages_to_users, so the real TelegramApi implementation
    # runs. It iterates over users and calls self.send_message_to_user(...) per user, which
    # MockApi does override and routes through call_mock to an AsyncMock. The assertion therefore
    # lands on the real mock and is meaningful.
    api.assert_send_message_to_user_called(user=owner, view=meeting_views.deletion_warning_view([meeting]))

    # The on_success callback fires after a successful send and sets this flag.
    assert meeting.expiration_notification_sent is True

    metrics.assert_emitted(name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_notify_unreachable_owner_marks_the_warning_as_sent(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    # Naive UTC, the shape the expiration column reads back with.
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=WARNING_DAYS + 2)

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    assert owner.status is UserStatus.LEFT
    # The warning could never be delivered, so the meeting moves on to the deletion pool.
    assert meeting.expiration_notification_sent is True

    record = residue_record(caplog, "Expiration warning undelivered")
    assert record.__dict__["reason"] == ResidueReason.OWNER_UNREACHABLE.value
    assert record.__dict__["meeting_id"] == 1
    assert record.__dict__["days_overdue"] == 2

    metrics.assert_emitted(name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)


async def test_notify_unreachable_owner_meeting_is_not_warned_again(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await warn_meeting_deletions.run(api, metrics_client)
    sends_after_first_run = api.mock_method("send_message_to_user").call_count

    # The next day's run re-derives its nomination set from the same predicate, which selects on
    # expiration_notification_sent being false.
    still_unwarned = tuple(candidate for candidate in (meeting,) if not candidate.expiration_notification_sent)
    mock_session.statements_registry.clear()
    register_nominated(mock_session, STATEMENT, still_unwarned)

    await warn_meeting_deletions.run(api, metrics_client)

    assert still_unwarned == ()
    assert api.mock_method("send_message_to_user").call_count == sends_after_first_run


async def test_notify_failed_send_leaves_the_meeting_in_the_warning_pool(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting = create_meetup(id=1, title="Unlucky Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    meeting.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=WARNING_DAYS + 1)

    register_nominated(mock_session, STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Bad Request: chat is temporarily unavailable")

    await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    # A transient failure keeps the meeting in the warning pool: it stays eligible for the next run.
    assert meeting.expiration_notification_sent is False

    record = residue_record(caplog, "Expiration warning failed")
    assert record.__dict__["reason"] == ResidueReason.OWNER_NOTIFICATION_FAILED.value
    assert record.__dict__["meeting_id"] == 1
    assert record.__dict__["days_overdue"] == 1

    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=1,
        properties={"failed_meeting_ids": [1]},
        dimensions=SWEEP_DIMENSIONS,
    )


def test_the_warning_statement_carries_a_branch_per_tier_window():
    """The warning window is rendered from the policy per owner tier. Which meetings it selects is
    covered in tests/data/db_behavior/test_lifecycle_windows.py."""
    compiled = " ".join(
        str(STATEMENT.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).split()
    )

    windows = LifecyclePolicy.levels_by_duration(lambda policy: policy.deletion_warning_delay)
    for duration, levels in windows.items():
        rendered_levels = ", ".join(f"'{level.value}'" for level in levels)
        assert (
            f"users.supporter_level IN ({rendered_levels}) AND meetups.expiration_time "
            f"+ CAST('{LifecyclePolicy.interval_days(duration)} days' AS INTERVAL) < now()" in compiled
        )


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


async def test_warning_record_tells_a_delivered_notice_from_an_undeliverable_one(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """`expiration_notification_sent = True` is the precondition for the later permanent deletion
    and is written for both dispositions, so this line is the only place they stay apart."""
    reached = create_meetup(id=1, title="Reached Owner")
    create_user(id=1, tg_user_id=10, owned_meetings=[reached], settings=create_settings(id=1))
    blocked = create_meetup(id=2, title="Blocked Owner")
    create_user(id=2, tg_user_id=20, owned_meetings=[blocked], settings=create_settings(id=2))

    register_nominated(mock_session, STATEMENT, (reached, blocked))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    with capture_logs(processors=[merge_contextvars]) as logs:
        await warn_meeting_deletions.run(api, metrics_client)

    records = {entry["meeting_id"]: entry for entry in logs if entry["event"] == "Meetup deletion warning recorded"}
    assert records[1]["delivery"] == warn_meeting_deletions.WarningDelivery.DELIVERED.value
    assert records[2]["delivery"] == warn_meeting_deletions.WarningDelivery.UNREACHABLE.value
    assert records[1]["tg_user_id"] == 10
    assert records[1]["window_days"] == WARNING_DAYS
    assert reached.expiration_notification_sent is True
    assert blocked.expiration_notification_sent is True


async def test_the_warning_sweep_stamps_when_it_warned_for_both_dispositions(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The stamp is what holds the deletion off for a full lead afterwards, so it has to be written
    wherever the flag is. An undeliverable notice sets both exactly like a delivered one: the meeting
    still moves to the deletion pool, and it still owes its owner the lead it promised."""
    reached = create_meetup(id=1, title="Reached Owner")
    create_user(id=1, tg_user_id=10, owned_meetings=[reached], settings=create_settings(id=1))
    blocked = create_meetup(id=2, title="Blocked Owner")
    create_user(id=2, tg_user_id=20, owned_meetings=[blocked], settings=create_settings(id=2))

    register_nominated(mock_session, STATEMENT, (reached, blocked))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    before = dt.datetime.now(dt.UTC)
    await warn_meeting_deletions.run(api, metrics_client)
    after = dt.datetime.now(dt.UTC)

    for meeting in (reached, blocked):
        assert meeting.expiration_notification_sent is True
        assert meeting.warned_time is not None
        assert before <= meeting.warned_time <= after


async def test_an_owner_who_turned_the_warning_off_is_not_written_to_but_the_meeting_moves_on(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """Turning the warning off opts out of the message, never out of the deletion: the meeting is
    recorded as warned exactly as an unreachable owner's is, so it reaches the deletion pool and is
    not re-nominated every day."""
    meeting = create_meetup(id=1, title="Quietly Expiring")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1, deletion_warning=False),
    )

    register_nominated(mock_session, STATEMENT, (meeting,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await warn_meeting_deletions.run(api, metrics_client)

    api.assert_method_just_called("send_message_to_user", times=0)
    assert meeting.expiration_notification_sent is True
    assert meeting.warned_time is not None

    record = next(entry for entry in logs if entry["event"] == "Meetup deletion warning recorded")
    assert record["delivery"] == warn_meeting_deletions.WarningDelivery.OPTED_OUT.value
    assert record["meeting_id"] == 1


async def test_the_warning_sweep_counts_the_owners_who_turned_it_off(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    silent = create_meetup(id=1, title="Opted Out")
    create_user(id=1, tg_user_id=10, owned_meetings=[silent], settings=create_settings(id=1, deletion_warning=False))
    warned = create_meetup(id=2, title="Still Warned")
    owner = create_user(id=2, tg_user_id=20, owned_meetings=[warned], settings=create_settings(id=2))

    register_nominated(mock_session, STATEMENT, (silent, warned))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await warn_meeting_deletions.run(api, metrics_client)

    api.assert_send_message_to_user_called(user=owner, view=meeting_views.deletion_warning_view([warned]))
    api.assert_method_just_called("send_message_to_user", times=1)

    sweep = next(entry for entry in logs if entry["event"] == "Deletion warning sweep complete")
    assert (sweep["nominated"], sweep["delivered"], sweep["opted_out"], sweep["unreachable"]) == (2, 1, 1, 0)


# ---------------------------------------------------------------------------
# One digest per owner
# ---------------------------------------------------------------------------


async def test_an_owner_with_several_expiring_meetings_is_warned_once(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The warning is one digest naming every nominated meeting, and every meeting it named is
    recorded as warned by that single send."""
    meetings = owned_meetups(1, 3, first_meeting_id=1)
    register_nominated(mock_session, STATEMENT, tuple(meetings))

    await warn_meeting_deletions.run(api, metrics_client)

    api.assert_send_message_to_user_called(user=meetings[0].owner, view=meeting_views.deletion_warning_view(meetings))
    assert all(meeting.expiration_notification_sent for meeting in meetings)


async def test_every_owner_gets_a_digest_of_their_own_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    first = owned_meetups(1, 2, first_meeting_id=1)
    second = owned_meetups(2, 1, first_meeting_id=3)
    # Interleaved, the order the statement can return them in.
    nominated = (first[0], second[0], first[1])
    register_nominated(mock_session, STATEMENT, nominated)

    await warn_meeting_deletions.run(api, metrics_client)

    api.assert_method_just_called("send_message_to_user", times=2)
    sent = {call.kwargs["user"].db_id: call.kwargs["view"] for call in api.call_args_list("send_message_to_user")}
    assert sent[1] == meeting_views.deletion_warning_view(first)
    assert sent[2] == meeting_views.deletion_warning_view(second)


async def test_a_digest_of_more_than_five_meetings_warns_every_one_of_them(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The digest names only its first few meetings, but the warning it records covers all of them:
    a meeting left out of the list still moves on to the deletion pool."""
    meetings = owned_meetups(1, 8, first_meeting_id=1)
    register_nominated(mock_session, STATEMENT, tuple(meetings))

    await warn_meeting_deletions.run(api, metrics_client)

    api.assert_method_just_called("send_message_to_user", times=1)
    assert all(meeting.expiration_notification_sent for meeting in meetings)


async def test_a_failed_warning_digest_leaves_every_meeting_it_named_for_the_next_run(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meetings = owned_meetups(1, 3, first_meeting_id=1)
    register_nominated(mock_session, STATEMENT, tuple(meetings))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Bad Request: chat is temporarily unavailable")

    await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    assert not any(meeting.expiration_notification_sent for meeting in meetings)
    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=3,
        properties={"failed_meeting_ids": [1, 2, 3]},
        dimensions=SWEEP_DIMENSIONS,
    )


async def test_the_warning_summary_counts_owners_beside_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A run's volume is owners messaged, not meetings nominated: with the digest the two numbers
    part company, and only the first one says how many messages went out."""
    reached = owned_meetups(1, 3, first_meeting_id=1)
    blocked = owned_meetups(2, 2, first_meeting_id=4)
    silent = create_meetup(id=6, title="Opted Out")
    create_user(id=3, tg_user_id=30, owned_meetings=[silent], settings=create_settings(id=3, deletion_warning=False))

    register_nominated(mock_session, STATEMENT, (*reached, *blocked, silent))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    with capture_logs(processors=[merge_contextvars]) as logs:
        await warn_meeting_deletions.run(api, metrics_client)

    sweep = next(entry for entry in logs if entry["event"] == "Deletion warning sweep complete")
    assert (sweep["delivered"], sweep["unreachable"], sweep["opted_out"]) == (3, 2, 1)
    assert (sweep["owners_messaged"], sweep["owners_unreachable"], sweep["owners_opted_out"]) == (1, 1, 1)
    assert (sweep["owners_failed"], sweep["failed"]) == (0, 0)


# ---------------------------------------------------------------------------
# One transaction per chunk of owners
# ---------------------------------------------------------------------------


async def test_the_warning_sweep_commits_one_transaction_per_chunk_of_owners(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """The sweep commits as it goes rather than once at the end: the nomination read, then one
    transaction per chunk of owners. Every nominated owner is still written to exactly once."""
    meetings = over_one_chunk()
    chunks = register_nominated(mock_session, STATEMENT, meetings)

    await warn_meeting_deletions.run(api, metrics_client)

    assert len(chunks) == 3
    api.assert_method_just_called("send_message_to_user", times=len(meetings))
    assert transaction_count(mock_session) == 1 + len(chunks)


async def test_a_failed_chunk_leaves_the_chunks_around_it_committed(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A chunk that cannot open its transaction costs its own owners and nothing else: the chunks
    before and after it are warned and marked, and the rows it left unmarked are exactly what the
    next daily run re-nominates."""
    meetings = over_one_chunk()
    chunks = register_nominated(mock_session, STATEMENT, meetings)
    # The nomination read is the first transaction, so the second chunk is the third.
    fail_transaction(mock_session, 3)

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(RuntimeError, match="cleanup chunks"):
            await warn_meeting_deletions.run(api, metrics_client)

    lost_owners = chunks[1]
    assert all(meeting.expiration_notification_sent is (meeting.owner.db_id not in lost_owners) for meeting in meetings)
    api.assert_method_just_called("send_message_to_user", times=len(meetings) - len(lost_owners))

    sweep = next(entry for entry in logs if entry["event"] == "Deletion warning sweep complete")
    assert (sweep["nominated"], sweep["delivered"]) == (len(meetings), len(meetings) - len(lost_owners))
    assert (sweep["chunks"], sweep["failed_chunks"]) == (len(chunks), 1)


async def test_a_chunk_whose_commit_is_lost_is_counted_as_neither_warned_nor_delivered(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    """The digests of a chunk whose commit is lost did reach their owners, but the sweep counts only
    what it committed, and the marks went down with the transaction. Those owners are re-nominated
    tomorrow and written to a second time: an understated mark costs a duplicate digest, never a
    deletion nobody was warned about."""
    meetings = over_one_chunk()
    chunks = register_nominated(mock_session, STATEMENT, meetings)
    fail_transaction(mock_session, 3, on_commit=True)

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(RuntimeError, match="cleanup chunks"):
            await warn_meeting_deletions.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=len(meetings))

    sweep = next(entry for entry in logs if entry["event"] == "Deletion warning sweep complete")
    assert (sweep["delivered"], sweep["failed_chunks"]) == (len(meetings) - len(chunks[1]), 1)

    failure = next(entry for entry in logs if entry["event"] == "Cleanup chunk failed")
    assert failure["log_level"] == "error"
    assert failure["reason"] == "chunk_transaction_failed"
    assert failure["owners"] == len(chunks[1])

    # The sweep still closes on the counters it owes before it raises: a lost commit is not a send
    # that failed.
    metrics.assert_emitted(name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED, value=0, dimensions=SWEEP_DIMENSIONS)
