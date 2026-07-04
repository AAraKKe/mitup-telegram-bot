import pytest

from mitup_bot.cli import notify_meetings
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.models import JoinedUsers
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from tests.helpers import MockApi, MockDbSession, create_joined_link, create_meetup, create_settings, create_user
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

# Inactive-user handling is not exercised here: under the write lifecycle an unreachable
# participant surfaces at drain time and is marked inactive by the reconcile transaction —
# see the reconcile tests in tests/test_db.py and the real-Postgres lifecycle tests in
# tests/models/db_behavior/test_cli_write_lifecycle.py.


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
    # - The meeting start time is between now and now + notification_time on the user settings

    expected_query = """SELECT
    joined_users.id,
    joined_users.user_id,
    joined_users.meetup_id,
    joined_users.invited_by_id,
    joined_users.created_time,
    joined_users.is_waiting_list,
    joined_users.notification_sent
FROM joined_users
    JOIN meetups ON meetups.id = joined_users.meetup_id
    JOIN users ON users.id = meetups.owner_id
    JOIN settings ON users.id = settings.user_id
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


async def test_meeting_start(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
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

    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_TO_SEND,
        value=2,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_SENT,
        value=2,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


async def test_link_no_longer_due_is_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
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

    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_TO_SEND,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_SENT,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


async def test_non_forbidden_exception_is_logged_and_loop_continues(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
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

    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_TO_SEND,
        value=2,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_SENT,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.NOTIFICATIONS_FAILED,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


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
