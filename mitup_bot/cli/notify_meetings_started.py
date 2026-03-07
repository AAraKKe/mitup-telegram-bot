import logging
from asyncio import gather
from contextlib import contextmanager

from aws_embedded_metrics.unit import Unit
from sqlmodel import Session, and_, false, func, null, select, true
from sqlmodel.sql.expression import SelectOfScalar
from telegram.error import Forbidden

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView

MEETINGS_TO_NOTIFY_STARTED_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.active == true(),
        Meetup.datetime != null(),
        Meetup.datetime <= func.now(),
        Meetup.started_notification_sent == false(),
    )
)


@contextmanager
def handle_forbidden(joined_link: JoinedUsers):
    try:
        yield
    except Forbidden:
        joined_link.user.is_active = False
        joined_link.notification_sent = True


async def send_started_notification(joined_link: JoinedUsers, api: TelegramApiWrapper) -> None:
    view = MitupView(
        description=NotificationMessages.MEETING_STARTED.get(
            lang=joined_link.user.lang, meeting_title=joined_link.meetup.title
        ),
        keyboard=[],
    )
    with handle_forbidden(joined_link):
        await api.send_message_to_user(joined_link.user, view)


@db.with_async_session
async def run(session: Session, api: TelegramApiWrapper, metrics: MitupMetricsLogger) -> None:
    """Send a notification to all participants when a meeting's scheduled time has arrived."""
    meetings = session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT).all()
    meetings_processed = 0
    sent = 0
    failed = 0
    failed_details: list[str] = []

    metrics.put_metric(MetricKey.MEETINGS_STARTED_PROCESSED.value, len(meetings), unit=Unit.COUNT.value)

    for meeting in meetings:
        try:
            participants = [link for link in meeting.joined_links if not link.is_waiting_list]
            notifications = [send_started_notification(link, api) for link in participants]
            results = await gather(*notifications, return_exceptions=True)

            for joined_link, result in zip(participants, results, strict=False):
                if isinstance(result, Exception):
                    failed += 1
                    logging.error(
                        f"Failed to send started notification (user: {joined_link.user_id}, "
                        f"meeting: {meeting.id}): {result}",
                        exc_info=result,
                    )
                else:
                    sent += 1

            await api.update_meeting_messages(session=session, meeting=meeting, has_started=True)
            meeting.started_notification_sent = True
            meetings_processed += 1
        except Exception as error:
            failed += 1
            logging.exception(
                f"Failed to process started notification for meeting (meeting: {meeting.id}, "
                f"owner: {meeting.owner_id}): {error}"
            )
            failed_details.append(
                f"Failed to process meeting (meeting: {meeting.id}, owner: {meeting.owner_id}): {error}"
            )

    if failed_details:
        metrics.set_property("failed_details", failed_details)

    metrics.put_metric(MetricKey.STARTED_NOTIFICATIONS_SENT.value, sent, unit=Unit.COUNT.value)
    metrics.put_metric(MetricKey.STARTED_NOTIFICATIONS_FAILED.value, failed, unit=Unit.COUNT.value)

    if failed:
        raise RuntimeError(f"Failed to process started notifications for {failed} items. Check logs for more details.")
