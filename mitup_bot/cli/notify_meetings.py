import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView

log = structlog.get_logger(__name__)

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


@db.with_session
async def due_link_ids(session: AsyncSession) -> list[int]:
    """Collect the ids of the joined links whose starting-soon notifications are due.

    Ids only, in a short read-only transaction: each link is processed later in its own
    write lifecycle, which re-checks that the notification is still due.
    """
    return [link.db_id for link in (await session.exec(USERS_TO_NOTIFY_STATEMENT)).all()]


async def notify_joined_link(link_id: int, api: TelegramApiWrapper) -> bool:
    """Send one participant's starting-soon notification and flag the link; returns False
    when it is no longer due.

    This job's unit of work is the joined link (the `notification_sent` flag lives there),
    so the write lifecycle wraps one link at a time: the send is enqueued inside the
    transaction and drains only after the flag committed — no transaction is held across
    Telegram I/O, and a crash mid-sweep cannot re-notify this participant on the next run.
    An unreachable user is marked inactive by the lifecycle's reconcile step.
    """
    async with db.begin_write(api) as session:
        # Re-check under the fresh transaction: the link may have been flagged or its
        # meeting rescheduled out of the window since the unlocked sweep nominated it.
        link = (await session.exec(USERS_TO_NOTIFY_STATEMENT.where(JoinedUsers.id == link_id))).first()
        if link is None:
            log.info("Joined link no longer due for a notification, skipping", joined_link=link_id)
            return False

        view = MitupView(
            description=NotificationMessages.STARTING_SOON.get(lang=link.user.lang, meeting_title=link.meetup.title),
            keyboard=[],
        )
        await api.send_message_to_user(link.user, view)
        link.notification_sent = True
        return True


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Send a notification to all users that have joined a meeting that is about to start.

    Each link commits in its own transaction: one participant's failure cannot roll back
    the notifications already committed for the others, and a crash mid-sweep resumes
    cleanly on the next run.
    """
    link_ids = await due_link_ids()
    metrics.emit(MetricKey.NOTIFICATIONS_TO_SEND, len(link_ids), MetricUnit.COUNT)

    sent = 0
    failed = 0
    for link_id in link_ids:
        try:
            if await notify_joined_link(link_id, api):
                sent += 1
        except Exception as error:
            failed += 1
            log.exception("Failed to send notification", joined_link=link_id, exc_info=error)

    metrics.emit(MetricKey.NOTIFICATIONS_SENT, sent, MetricUnit.COUNT)
    metrics.emit(MetricKey.NOTIFICATIONS_FAILED, failed, MetricUnit.COUNT)

    if failed:
        raise RuntimeError(f"Failed to send notification to {failed} users. Check logs for more details.")
