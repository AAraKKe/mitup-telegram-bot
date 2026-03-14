import pytest
from aws_embedded_metrics.unit import Unit
from telegram.error import Forbidden

from mitup_bot.cli import notify_meetings
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.models import JoinedUsers
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from tests.helpers import MockApi, MockDbSession, StubMetrics, create_meetup, create_settings, create_user


@pytest.fixture
def metrics() -> StubMetrics:
    metrics = StubMetrics([])
    metrics.set_dimensions({"EventType": EventType.NOTIFY_START_MEETING.value})
    return metrics


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
    AND users.is_active = true
    AND settings.notification = true
    AND joined_users.is_waiting_list = false
    AND joined_users.notification_sent = false
    AND now() BETWEEN
        meetups.datetime - CAST(concat(settings.notification_time, ' minutes') AS INTERVAL)
        AND meetups.datetime"""

    notify_meetings.joined_links_to_notify(mock_session)
    assert mock_session.normalize_query(expected_query) == mock_session.queries_executed[0]


async def test_meeting_start(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str) -> None:
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)
    link_2 = JoinedUsers(user=joined_2, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1, link_2))
    await notify_meetings.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    assert link_1.notification_sent
    assert link_2.notification_sent

    view1 = MitupView(
        description=NotificationMessages.MEETING_STARTING.get(lang=joined_1.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    view2 = MitupView(
        description=NotificationMessages.MEETING_STARTING.get(lang=joined_2.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    api.assert_send_message_to_user_called(user=joined_1, view=view1, times=2)
    api.assert_send_message_to_user_called(user=joined_2, view=view2, times=2)

    metrics.assert_metrics_emited(
        [
            MetricKey.NOTIFICATIONS_TO_SEND,
            MetricKey.NOTIFICATIONS_SENT,
            MetricKey.NOTIFICATIONS_FAILED,
            MetricKey.INACTIVE_USER_SET,
        ],
        [2, 2, 0, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


async def test_non_forbidden_exception_is_logged_and_loop_continues(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    """A non-Forbidden exception from send_message_to_user is counted as failed and logged.

    The exception is NOT re-raised immediately — the loop continues processing the next
    joined_link.  After the loop, because failed > 0, a RuntimeError is raised.
    """
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)
    link_2 = JoinedUsers(user=joined_2, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1, link_2))

    send_message_mock = api.mock_method("send_message_to_user")
    # First call raises a generic (non-Forbidden) exception; second succeeds.
    send_message_mock.side_effect = [Exception("Network error"), None]

    with pytest.raises(RuntimeError, match="Failed to send notification to 1 users"):
        await notify_meetings.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759

    await metrics.flush()

    # The second link was still notified — the loop did not abort on the first failure.
    assert link_2.notification_sent

    metrics.assert_metrics_emited(
        [
            MetricKey.NOTIFICATIONS_TO_SEND,
            MetricKey.NOTIFICATIONS_SENT,
            MetricKey.NOTIFICATIONS_FAILED,
            MetricKey.INACTIVE_USER_SET,
        ],
        [2, 1, 1, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


async def test_failed_greater_than_zero_raises_runtime_error(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    """When failed > 0 after the notification loop, RuntimeError is raised."""
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1,))

    send_message_mock = api.mock_method("send_message_to_user")
    send_message_mock.side_effect = Exception("Unexpected error")

    with pytest.raises(RuntimeError, match="Failed to send notification to 1 users. Check logs for more details."):
        await notify_meetings.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759


async def test_forbidden_message_sent(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str):
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)
    link_2 = JoinedUsers(user=joined_2, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1, link_2))

    # Need to access low level mock, still do not have a way of mocking the api call directly
    send_message_mock = api.mock_method("send_message_to_user")
    send_message_mock.side_effect = [Forbidden("Nope"), None]
    await notify_meetings.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    assert link_1.notification_sent
    assert link_2.notification_sent

    view1 = MitupView(
        description=NotificationMessages.MEETING_STARTING.get(lang=joined_1.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    view2 = MitupView(
        description=NotificationMessages.MEETING_STARTING.get(lang=joined_2.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    api.assert_send_message_to_user_called(user=joined_1, view=view1, times=2)
    api.assert_send_message_to_user_called(user=joined_2, view=view2, times=2)

    metrics.assert_metrics_emited(
        [
            MetricKey.NOTIFICATIONS_TO_SEND,
            MetricKey.NOTIFICATIONS_SENT,
            MetricKey.NOTIFICATIONS_FAILED,
            MetricKey.INACTIVE_USER_SET,
        ],
        [2, 2, 0, 1],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
