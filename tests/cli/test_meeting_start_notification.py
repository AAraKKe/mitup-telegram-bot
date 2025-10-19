from unittest import mock

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import InlineKeyboardMarkup
from telegram.error import Forbidden
from telegram.ext import ExtBot

from mitup_bot.cli import notify_meetings
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.models import JoinedUsers
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.messages import NotificationMessages
from tests.helpers import MockDbSession, StubMetrics, create_meetup, create_settings, create_user


@pytest.fixture
def metrics() -> StubMetrics:
    metrics = StubMetrics([])
    metrics.set_dimensions({"EventType": EventType.NOTIFY_START_MEETING.value})
    return metrics


@pytest.fixture
def bot():
    bot = mock.MagicMock(spec=ExtBot)
    bot.defaults = None
    return bot


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
    joined_users.created_time,
    joined_users.is_waiting_list,
    joined_users.notification_sent
FROM joined_users
    JOIN meetups ON meetups.id = joined_users.meetup_id
    JOIN users ON users.id = meetups.owner_id
    JOIN settings ON users.id = settings.user_id
WHERE meetups.datetime IS NOT NULL
    AND users.is_active = true
    AND joined_users.is_waiting_list = false
    AND joined_users.notification_sent = false
    AND now() BETWEEN
        meetups.datetime - CAST(concat(settings.notification_time, ' minutes') AS INTERVAL)
        AND meetups.datetime"""

    notify_meetings.joined_links_to_notify(mock_session)
    assert mock_session.normalize_query(expected_query) == mock_session.queries_executed[0]


async def test_meeting_start(mock_session: MockDbSession, metrics: StubMetrics, bot: mock.MagicMock, lang: str) -> None:
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)
    link_2 = JoinedUsers(user=joined_2, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1, link_2))
    await notify_meetings.run(bot, metrics)
    await metrics.flush()

    assert link_1.notification_sent
    assert link_2.notification_sent
    bot.send_message.assert_has_calls(
        [
            mock.call(
                chat_id=1,
                text=NotificationMessages.MEETING_STARTING.get(lang=joined_1.lang, meeting_title=meeting.title),
                reply_markup=InlineKeyboardMarkup([]),
            ),
            mock.call(
                chat_id=2,
                text=NotificationMessages.MEETING_STARTING.get(lang=joined_2.lang, meeting_title=meeting.title),
                reply_markup=InlineKeyboardMarkup([]),
            ),
        ],
        any_order=True,
    )
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


async def test_forbidden_message_sent(
    mock_session: MockDbSession, metrics: StubMetrics, bot: mock.MagicMock, lang: str
):
    meeting = create_meetup(id=1, title="Test meetup")
    joined_1 = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    joined_2 = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    link_1 = JoinedUsers(user=joined_1, meetup=meeting)
    link_2 = JoinedUsers(user=joined_2, meetup=meeting)

    mock_session.add_objects_with_statement(notify_meetings.USERS_TO_NOTIFY_STATEMENT, (link_1, link_2))
    bot.send_message.side_effect = [Forbidden("Nope"), None]
    await notify_meetings.run(bot, metrics)
    await metrics.flush()

    assert link_1.notification_sent
    assert link_2.notification_sent
    bot.send_message.assert_has_calls(
        [
            mock.call(
                chat_id=1,
                text=NotificationMessages.MEETING_STARTING.get(lang=joined_1.lang, meeting_title=meeting.title),
                reply_markup=InlineKeyboardMarkup([]),
            ),
            mock.call(
                chat_id=2,
                text=NotificationMessages.MEETING_STARTING.get(lang=joined_2.lang, meeting_title=meeting.title),
                reply_markup=InlineKeyboardMarkup([]),
            ),
        ],
        any_order=True,
    )

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
