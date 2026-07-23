import structlog
from sqlmodel import and_, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

log = structlog.get_logger(__name__)

MEETINGS_TO_NOTIFY_STARTED_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.active == true(),
        Meetup.datetime != null(),
        Meetup.datetime <= func.now(),
        Meetup.started_notification_sent == false(),
    )
)


@db.with_session
async def due_meeting_ids(session: AsyncSession) -> list[int]:
    """Collect the ids of the meetings whose started notifications are currently due.

    Ids only, in a short read-only transaction: each meeting is processed later in its own
    write lifecycle, which re-checks that the notification is still due.
    """
    return [meeting.db_id for meeting in (await session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT)).all()]


async def notify_meeting_started(meetup_id: int, api: TelegramApiWrapper) -> int:
    """Notify one meeting's participants and flag it notified; returns the number of
    participants notified (0 when the meeting is no longer due).

    The write lifecycle enqueues the notifications and message edits inside the
    transaction and drains them only after `started_notification_sent` committed: no
    transaction is held across Telegram I/O, and a crash mid-sweep cannot re-notify this
    meeting on the next run. Unreachable participants are marked inactive by the
    lifecycle's reconcile step.
    """
    async with db.begin_write(api) as session:
        # Re-check under the fresh transaction: the meeting may have been deactivated or
        # flagged since the unlocked sweep nominated it.
        meeting = (await session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT.where(Meetup.id == meetup_id))).first()
        if meeting is None:
            log.info("Meeting no longer due for a started notification, skipping", meeting=meetup_id)
            return 0

        participants = [
            link for link in meeting.joined_links if not link.is_waiting_list and link.user.status is UserStatus.MEMBER
        ]
        for link in participants:
            view = MitupView(
                description=NotificationMessages.STARTED.get(lang=link.user.lang, meeting_title=rich_title(meeting)),
                keyboard=[],
            )
            await api.send_message_to_user(link.user, view)

        await api.update_meeting_messages(meeting=meeting)
        meeting.started_notification_sent = True
        return len(participants)


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Send a notification to all participants when a meeting's scheduled time has arrived.

    Each meeting commits in its own transaction: one meeting's failure cannot roll back the
    notifications already committed for the others, and a crash mid-sweep resumes cleanly
    on the next run.
    """
    meeting_ids = await due_meeting_ids()
    metrics.emit(MetricKey.MEETINGS_STARTED_PROCESSED, len(meeting_ids), MetricUnit.COUNT)

    sent = 0
    failed = 0
    failed_details: list[str] = []

    for meetup_id in meeting_ids:
        try:
            sent += await notify_meeting_started(meetup_id, api)
        except Exception as error:
            failed += 1
            log.exception("Failed to process started notification for meeting", meeting=meetup_id, exc_info=error)
            failed_details.append(f"Failed to process meeting (meeting: {meetup_id}): {error}")

    metrics.emit(MetricKey.STARTED_NOTIFICATIONS_SENT, sent, MetricUnit.COUNT)
    metrics.emit(
        MetricKey.STARTED_NOTIFICATIONS_FAILED,
        failed,
        MetricUnit.COUNT,
        properties={"failed_details": failed_details} if failed_details else None,
    )

    if failed:
        raise RuntimeError(f"Failed to process started notifications for {failed} meetings. Check logs for details.")
