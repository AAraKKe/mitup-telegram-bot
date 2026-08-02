import datetime as dt
from collections.abc import Callable

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.events import notify_meetings
from mitup_bot.events.service import EventType
from mitup_bot.models import JoinedUsers
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from tests.helpers import MockApi, MockDbSession, create_joined_link, create_meetup, create_settings, create_user
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

# Inactive-user handling is not exercised here: under the write lifecycle an unreachable
# participant surfaces at drain time and is marked inactive by the reconcile transaction —
# see the reconcile tests in tests/test_db.py and the real-Postgres lifecycle tests in
# tests/models/db_behavior/test_events_write_lifecycle.py.


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.NOTIFY_START_MEETING.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def register_due_links(mock_session: MockDbSession, *links: JoinedUsers, still_due: bool = True):
    """Register a link for both reads the job performs: the unlocked candidate sweep and
    the per-link re-check inside the write lifecycle (empty when ``still_due`` is False —
    the link was flagged or its meeting rescheduled between sweep and processing)."""
    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, links)
    for link in links:
        mock_session.add_objects_with_statement(
            notify_meetings.USERS_TO_NOTIFY_STATEMENT.where(JoinedUsers.id == link.id),
            (link,) if still_due else (),
        )


def test_query_for_users_to_notify_about_meeting_start(mock_session: MockDbSession):
    # The query must select all joined_users that:
    # - Are not in the waiting list
    # - The meeting has a datetime set
    # - The notification has not yet been sent
    # - The meeting start time is between now and now + notification_time on the PARTICIPANT's settings

    expected_query = """SELECT
    joined_users.id,
    joined_users.user_id,
    joined_users.meetup_id,
    joined_users.invited_by_id,
    joined_users.created_time,
    joined_users.is_waiting_list,
    joined_users.notification_sent
FROM joined_users
    JOIN meetups ON joined_users.meetup_id = meetups.id
    JOIN users ON joined_users.user_id = users.id
    JOIN settings ON settings.user_id = users.id
WHERE meetups.datetime IS NOT NULL
    AND users.status = 'member'
    AND settings.notification = true
    AND joined_users.is_waiting_list = false
    AND joined_users.notification_sent = false
    AND now() BETWEEN
        meetups.datetime - CAST(concat(settings.notification_time, ' minutes') AS INTERVAL)
        AND meetups.datetime"""

    mock_session.exec(notify_meetings.USERS_TO_NOTIFY_STATEMENT)
    assert mock_session.normalize_query(expected_query) == mock_session.queries_executed[0]


def test_users_and_settings_join_through_the_participant_not_the_owner():
    """Regression for #201: FK inference used to route the users join through
    meetups.owner_id, so status, notification toggle, and lead-time window were all
    evaluated against the meeting owner instead of the participant being notified."""
    compiled = MockDbSession.normalize_query(str(notify_meetings.USERS_TO_NOTIFY_STATEMENT))

    assert "JOIN users ON joined_users.user_id = users.id" in compiled
    assert "JOIN settings ON settings.user_id = users.id" in compiled
    assert "meetups.owner_id" not in compiled


async def test_meeting_start(mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str):
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = create_joined_link(user=joined_1, meetup=meeting, id=1)
    link_2 = create_joined_link(user=joined_2, meetup=meeting, id=2)

    register_due_links(mock_session, link_1, link_2)
    await notify_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert link_1.notification_sent
    assert link_2.notification_sent

    view1 = MitupView(
        description=NotificationMessages.STARTING_SOON.get(lang=joined_1.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    view2 = MitupView(
        description=NotificationMessages.STARTING_SOON.get(lang=joined_2.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    api.assert_send_message_to_user_called(user=joined_1, view=view1, times=2)
    api.assert_send_message_to_user_called(user=joined_2, view=view2, times=2)


async def test_link_no_longer_due_is_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str
):
    """The per-link re-check found the link already flagged or its meeting rescheduled out
    of the notification window: nothing is sent, nothing fails — the next sweep
    re-evaluates from scratch."""
    meeting = create_meetup(id=1, title="Rescheduled meetup")
    joined = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    link = create_joined_link(user=joined, meetup=meeting, id=1)

    register_due_links(mock_session, link, still_due=False)
    await notify_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert link.notification_sent is False
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_non_forbidden_exception_is_logged_and_loop_continues(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str
):
    """An exception while processing one link is counted as failed and logged.

    The exception is NOT re-raised immediately — each link runs in its own write
    lifecycle, so the loop continues with the next one.  After the loop, because
    failed > 0, a RuntimeError is raised.
    """
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = create_joined_link(user=joined_1, meetup=meeting, id=1)
    link_2 = create_joined_link(user=joined_2, meetup=meeting, id=2)

    register_due_links(mock_session, link_1, link_2)

    send_message_mock = api.mock_method("send_message_to_user")
    # First call raises a generic exception; second succeeds.
    send_message_mock.side_effect = [Exception("Network error"), None]

    with pytest.raises(RuntimeError, match="Failed to send notification to 1 users"):
        await notify_meetings.run(api, metrics_client)

    await metrics_client.flush()

    # The first link's transaction rolled back with its failure; the second link was still
    # notified and committed — the loop did not abort on the first failure.
    assert link_1.notification_sent is False
    assert link_2.notification_sent


async def test_failed_greater_than_zero_raises_runtime_error(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str
):
    """When failed > 0 after the notification loop, RuntimeError is raised."""
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    link_1 = create_joined_link(user=joined_1, meetup=meeting, id=1)

    register_due_links(mock_session, link_1)

    send_message_mock = api.mock_method("send_message_to_user")
    send_message_mock.side_effect = Exception("Unexpected error")

    with pytest.raises(RuntimeError, match="Failed to send notification to 1 users. Check logs for more details."):
        await notify_meetings.run(api, metrics_client)


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


def register_stale_link(mock_session: MockDbSession, link: JoinedUsers):
    """Register a link the sweep nominated but the re-check rejects, plus the predicate-free read
    ``skip_reason`` uses to work out which of the six conditions stopped holding."""
    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link,))
    mock_session.add_objects_with_statement(
        notify_meetings.USERS_TO_NOTIFY_STATEMENT.where(JoinedUsers.id == link.id), ()
    )
    mock_session.add_object(link)


async def test_the_sweep_names_the_participant_at_every_step(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str
):
    """Nomination, send and the closing summary all carry the joined link, its meeting and the
    person whose reminder it is, so the commonest support question this job produces — "this user
    says they never got their reminder" — is answerable by filtering on the user."""
    meeting = create_meetup(id=7, title="Test meetup", datetime=dt.datetime.now(dt.UTC))
    settings = create_settings(id=1, language=lang)
    settings.notification_time = 15
    joined = create_user(id=1, tg_user_id=555, settings=settings)
    link = create_joined_link(user=joined, meetup=meeting, id=812)

    register_due_links(mock_session, link)
    with capture_logs(processors=[merge_contextvars]) as logs:
        await notify_meetings.run(api, metrics_client)

    nominated = next(
        entry for entry in logs if entry["event"] == "Nominate joined link for a starting-soon notification"
    )
    assert nominated["joined_link_id"] == 812
    assert nominated["meeting_id"] == 7
    assert nominated["tg_user_id"] == 555
    # The per-user setting that chose the window, so the nomination stays explicable after it changes.
    assert nominated["lead_time_minutes"] == 15
    assert nominated["reason"] == "inside_lead_time_window"

    sent = next(entry for entry in logs if entry["event"] == "Send starting-soon notification")
    # "enqueued", not "sent": the drain happens after the transaction commits.
    assert sent["outcome"] == "enqueued"
    assert sent["joined_link_id"] == 812
    assert sent["tg_user_id"] == 555

    summary = next(entry for entry in logs if entry["event"] == "Starting-soon notification sweep complete")
    assert (summary["nominated"], summary["sent"], summary["skipped"], summary["failed"]) == (1, 1, 0, 0)


def make_stale_link(lang: str) -> JoinedUsers:
    """A link that satisfied every nomination condition, ready for one of them to be flipped."""
    meeting = create_meetup(id=7, title="Test meetup", datetime=dt.datetime.now(dt.UTC))
    joined = create_user(id=1, tg_user_id=555, settings=create_settings(id=1, language=lang))
    return create_joined_link(user=joined, meetup=meeting, id=812)


def flag_already_notified(link: JoinedUsers):
    link.notification_sent = True


def move_to_waiting_list(link: JoinedUsers):
    link.is_waiting_list = True


def make_user_leave(link: JoinedUsers):
    link.user.status = UserStatus.LEFT


def disable_notifications(link: JoinedUsers):
    link.user.settings.notification = False


SKIP_REASON_PARAMS = [
    (flag_already_notified, "already_notified"),
    (move_to_waiting_list, "moved_to_waiting_list"),
    (make_user_leave, "user_not_member"),
    (disable_notifications, "notifications_disabled"),
]


@pytest.mark.parametrize(
    "break_condition, expected_reason", SKIP_REASON_PARAMS, ids=[reason for _, reason in SKIP_REASON_PARAMS]
)
async def test_skip_names_which_condition_stopped_holding(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    api: MockApi,
    lang: str,
    break_condition: Callable[[JoinedUsers], None],
    expected_reason: str,
):
    """Each cause of a skip names itself. They split into user-caused (turned notifications off,
    moved to the waiting list) and system-caused, and an operator has to know which one they are
    looking at before deciding whether anything is wrong."""
    link = make_stale_link(lang)
    break_condition(link)

    register_stale_link(mock_session, link)
    with capture_logs(processors=[merge_contextvars]) as logs:
        await notify_meetings.run(api, metrics_client)

    skipped = next(entry for entry in logs if entry["event"] == "Skip starting-soon notification")
    assert skipped["reason"] == expected_reason
    assert skipped["joined_link_id"] == 812
    assert skipped["tg_user_id"] == 555
    api.assert_method_just_called("send_message_to_user", times=0)


async def test_skip_reports_a_deleted_link(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi, lang: str
):
    """A link deleted between nomination and processing is the one skip cause that leaves no row to
    re-read, so it is named directly rather than derived from the row's state."""
    meeting = create_meetup(id=7, title="Test meetup", datetime=dt.datetime.now(dt.UTC))
    joined = create_user(id=1, tg_user_id=555, settings=create_settings(id=1, language=lang))
    link = create_joined_link(user=joined, meetup=meeting, id=812)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link,))
    mock_session.add_objects_with_statement(
        notify_meetings.USERS_TO_NOTIFY_STATEMENT.where(JoinedUsers.id == link.id), ()
    )

    with capture_logs(processors=[merge_contextvars]) as logs:
        await notify_meetings.run(api, metrics_client)

    skipped = next(entry for entry in logs if entry["event"] == "Skip starting-soon notification")
    assert skipped["reason"] == "link_deleted"
