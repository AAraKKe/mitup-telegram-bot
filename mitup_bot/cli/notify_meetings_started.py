from asyncio import gather
from contextlib import contextmanager

import structlog
from sqlmodel import and_, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar
from telegram.error import Forbidden

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView

log = structlog.get_logger(__name__)

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
        joined_link.user.mark_inactive()
        joined_link.notification_sent = True


async def send_started_notification(joined_link: JoinedUsers, api: TelegramApiWrapper) -> None:
    view = MitupView(
        description=NotificationMessages.STARTED.get(
            lang=joined_link.user.lang, meeting_title=joined_link.meetup.title
        ),
        keyboard=[],
    )
    with handle_forbidden(joined_link):
        await api.send_message_to_user(joined_link.user, view)


@db.with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient) -> None:
    """Send a notification to all participants when a meeting's scheduled time has arrived."""
    meetings = (await session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT)).all()
    meetings_processed = 0
    sent = 0
    failed = 0
    failed_details: list[str] = []

    metrics.emit(MetricKey.MEETINGS_STARTED_PROCESSED, len(meetings), MetricUnit.COUNT)

    for meeting in meetings:
        try:
            participants = [
                link
                for link in meeting.joined_links
                if not link.is_waiting_list and link.user.status is UserStatus.MEMBER
            ]
            notifications = [send_started_notification(link, api) for link in participants]
            results = await gather(*notifications, return_exceptions=True)

            for joined_link, result in zip(participants, results, strict=False):
                if isinstance(result, Exception):
                    failed += 1
                    log.error(
                        "Failed to send started notification",
                        user=joined_link.user_id,
                        meeting=meeting.id,
                        exc_info=result,
                    )
                else:
                    sent += 1

            await api.update_meeting_messages(session=session, meeting=meeting)
            meeting.started_notification_sent = True
            meetings_processed += 1
        except Exception as error:
            failed += 1
            log.exception(
                "Failed to process started notification for meeting",
                meeting=meeting.id,
                owner=meeting.owner_id,
                exc_info=error,
            )
            failed_details.append(
                f"Failed to process meeting (meeting: {meeting.id}, owner: {meeting.owner_id}): {error}"
            )

    metrics.emit(MetricKey.STARTED_NOTIFICATIONS_SENT, sent, MetricUnit.COUNT)
    metrics.emit(
        MetricKey.STARTED_NOTIFICATIONS_FAILED,
        failed,
        MetricUnit.COUNT,
        properties={"failed_details": failed_details} if failed_details else None,
    )

    if failed:
        raise RuntimeError(f"Failed to process started notifications for {failed} items. Check logs for more details.")
