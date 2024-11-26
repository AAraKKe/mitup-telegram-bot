from collections.abc import Sequence
from contextlib import contextmanager

from aws_embedded_metrics.unit import Unit
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Session, and_, false, func, null, select, true
from sqlmodel.sql.expression import SelectOfScalar
from telegram.error import Forbidden
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views import MitupView

USERS_TO_NOTIFY_STATEMENT: SelectOfScalar[JoinedUsers] = (
    select(JoinedUsers)
    .join(Meetup)
    .join(User)
    .join(Settings)
    .where(
        and_(
            Meetup.datetime != null(),
            User.is_active == true(),
            JoinedUsers.is_waiting_list == false(),
            JoinedUsers.notification_sent == false(),
            func.now().between(
                Meetup.datetime - func.cast(func.concat(Settings.notification_time, " minutes"), INTERVAL),
                Meetup.datetime,
            ),
        )
    )
)


def joined_links_to_notify(session: Session) -> Sequence[JoinedUsers]:
    """
    Returns all users to notify:
    - The user is not in the waiting list
    - The meeting has a datetime set
    - The meeting start time is between now and now + notification_time on the user settings
    """
    return session.exec(USERS_TO_NOTIFY_STATEMENT).all()


@contextmanager
def handle_forbidden(link: JoinedUsers):
    try:
        yield
    except Forbidden:
        link.user.is_active = False
        # If we cannot send notification lets avoid doing it again
        link.notification_sent = True


@db.with_async_session
async def run(session: Session, bot: ExtBot, metrics: MitupMetricsLogger) -> None:
    """Send a notification to all users that have joined a meeting that is about to start"""
    joined_links = joined_links_to_notify(session)
    deactivated_users = 0
    sent = 0

    metrics.put_metric(MetricKey.NOTIFICATIONS_TO_SEND.value, len(joined_links), unit=Unit.COUNT.value)

    try:
        for joined_link in joined_links:
            with handle_forbidden(joined_link):
                await joined_link.user.send_message(
                    bot,
                    MitupView(
                        description=MeetingMessages.NOTIFICATION_MEETING_STARTING.get(
                            lang=joined_link.user.lang, meeting_title=joined_link.meetup.title
                        ),
                        keyboard=[],
                    ),
                )
                joined_link.notification_sent = True
            # If we have deactivated the user, incremenet deactivate_users
            deactivated_users += not joined_link.user.is_active
            sent += 1
    finally:
        metrics.put_metric(MetricKey.MEETING_NOTIFICATIONS_SENT.value, sent, unit=Unit.COUNT.value)
        metrics.put_metric(MetricKey.INACTIVE_USER_SET.value, deactivated_users, unit=Unit.COUNT.value)
