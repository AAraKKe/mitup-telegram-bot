import logging
from asyncio import gather
from collections.abc import Sequence
from contextlib import contextmanager

from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Session, and_, false, func, null, select, true
from sqlmodel.sql.expression import SelectOfScalar
from telegram.error import Forbidden

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView

USERS_TO_NOTIFY_STATEMENT: SelectOfScalar[JoinedUsers] = (
    select(JoinedUsers)
    .join(Meetup)
    .join(User)
    .join(Settings)
    .where(
        and_(
            Meetup.datetime != null(),
            User.status == UserStatus.MEMBER,
            Settings.notification == true(),
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
    return session.exec(USERS_TO_NOTIFY_STATEMENT).all()


@contextmanager
def handle_forbidden(link: JoinedUsers):
    try:
        yield
    except Forbidden:
        link.user.mark_inactive()
        # If we cannot send notification lets avoid doing it again
        link.notification_sent = True


async def send_notification(joined_link: JoinedUsers, api: TelegramApiWrapper):
    view = MitupView(
        description=NotificationMessages.MEETING_STARTING.get(
            lang=joined_link.user.lang, meeting_title=joined_link.meetup.title
        ),
        keyboard=[],
    )
    with handle_forbidden(joined_link):
        await api.send_message_to_user(joined_link.user, view)

    joined_link.notification_sent = True


@db.with_async_session
async def run(session: Session, api: TelegramApiWrapper, metrics: MetricsClient) -> None:
    """Send a notification to all users that have joined a meeting that is about to start"""
    joined_links = joined_links_to_notify(session)
    failed = 0
    sent = 0

    metrics.emit(MetricKey.NOTIFICATIONS_TO_SEND, len(joined_links), MetricUnit.COUNT)

    pre_send_statuses = {link.user.id: link.user.status for link in joined_links}
    notifications = [send_notification(joined_link, api) for joined_link in joined_links]
    results = await gather(*notifications, return_exceptions=True)

    for joined_link, result in zip(joined_links, results, strict=False):
        if isinstance(result, Exception):
            failed += 1
            logging.error(
                f"Failed to send notification (user: {joined_link.user_id}, "
                f"meeting: {joined_link.meetup_id}): {result}",
                exc_info=result,
            )
        else:
            sent += 1

    deactivated_user_ids = {
        link.user.id
        for link in joined_links
        if pre_send_statuses[link.user.id] is UserStatus.MEMBER and link.user.status is UserStatus.LEFT
    }
    deactivated_users = len(deactivated_user_ids)

    metrics.emit(MetricKey.NOTIFICATIONS_SENT, sent, MetricUnit.COUNT)
    metrics.emit(MetricKey.NOTIFICATIONS_FAILED, failed, MetricUnit.COUNT)
    metrics.emit(MetricKey.INACTIVE_USER_SET, deactivated_users, MetricUnit.COUNT)

    if failed:
        raise RuntimeError(f"Failed to send notification to {failed} users. Check logs for more details.")
