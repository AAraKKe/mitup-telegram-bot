import datetime as dt
from dataclasses import asdict, dataclass

import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

from .telemetry import error_type_name

log = structlog.get_logger(__name__)

# The ON clauses are explicit because FK inference picks the users join through
# meetups.owner_id, silently evaluating every per-user condition (status, notification
# toggle, lead-time window) against the meeting OWNER instead of the participant.
USERS_TO_NOTIFY_STATEMENT: SelectOfScalar[JoinedUsers] = (
    select(JoinedUsers)
    .join(Meetup, col(JoinedUsers.meetup_id) == col(Meetup.id))
    .join(User, col(JoinedUsers.user_id) == col(User.id))
    .join(Settings, col(Settings.user_id) == col(User.id))
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


@dataclass(frozen=True, slots=True)
class DueLink:
    """A joined link nominated for a starting-soon notification, with the facts that chose it.

    The participant and their meeting are carried out of the nomination read so every later line of
    this link's trail — including the ones the post-commit drain emits — names the person whose
    reminder it is, and so the lead time that picked the window is on the record even after the
    setting changes. The field names are the log field names: they are logged via `asdict`.
    """

    joined_link_id: int
    meeting_id: int
    tg_user_id: int
    lead_time_minutes: int
    meeting_datetime: dt.datetime | None


@db.with_session
async def due_links(session: AsyncSession) -> list[DueLink]:
    """Nominate the joined links whose starting-soon notifications are due.

    A short read-only transaction: each link is processed later in its own write lifecycle, which
    re-checks that the notification is still due.
    """
    nominated = [
        DueLink(
            joined_link_id=link.db_id,
            meeting_id=link.meetup.db_id,
            tg_user_id=link.user.tg_user_id,
            lead_time_minutes=link.user.settings.notification_time,
            meeting_datetime=link.meetup.datetime,
        )
        for link in (await session.exec(USERS_TO_NOTIFY_STATEMENT)).all()
    ]
    for due in nominated:
        log.info(
            "Nominate joined link for a starting-soon notification", reason="inside_lead_time_window", **asdict(due)
        )
    return nominated


async def skip_reason(session: AsyncSession, joined_link_id: int) -> str:
    """Name which condition of the nomination predicate stopped holding, by re-reading the row
    outside the predicate and testing each condition in turn.

    The lead-time window is the residual: it is the only condition evaluated against the database
    clock, so it is reported once everything readable off the row still holds.
    """
    link = (await session.exec(select(JoinedUsers).where(col(JoinedUsers.id) == joined_link_id))).first()
    if link is None:
        return "link_deleted"
    if link.notification_sent:
        return "already_notified"
    if link.is_waiting_list:
        return "moved_to_waiting_list"
    if link.user.status is not UserStatus.MEMBER:
        return "user_not_member"
    if not link.user.settings.notification:
        return "notifications_disabled"
    if link.meetup.datetime is None:
        return "meeting_datetime_cleared"
    return "outside_lead_time_window"


def starting_soon_view(link: JoinedUsers) -> MitupView:
    """Render the reminder from the columns the re-check already loaded.

    Reads only `link.user.lang` and the title columns of `link.meetup`, both loaded (the JoinedUsers
    root loads `user` and `meetup` via mapper-level selectin). `link.meetup.joined_links` is NOT
    loaded: from a JoinedUsers root the cascade revisits the JoinedUsers mapper on
    `meetup -> joined_links` and stops, and unlike `meetup` this collection has no identity-map
    rescue — any access emits SQL and raises MissingGreenlet here. A future `update_meeting_messages`
    call (which iterates the participant list) must therefore chain
    `JoinedUsers.meetup -> Meetup.joined_links -> JoinedUsers.user`/`invited_by` onto the re-query
    first; the shared `USERS_TO_NOTIFY_STATEMENT` stays lean because the nomination reads no more.
    """
    return MitupView(
        description=NotificationMessages.STARTING_SOON.get(lang=link.user.lang, meeting_title=rich_title(link.meetup)),
        keyboard=[],
    )


async def notify_joined_link(due: DueLink, api: TelegramApiWrapper) -> bool:
    """Send one participant's starting-soon notification and flag the link; returns False
    when it is no longer due.

    This job's unit of work is the joined link (the `notification_sent` flag lives there),
    so the write lifecycle wraps one link at a time: the send is enqueued inside the
    transaction and drains only after the flag committed — no transaction is held across
    Telegram I/O, and a crash mid-sweep cannot re-notify this participant on the next run.
    An unreachable user is marked inactive by the lifecycle's reconcile step.
    """
    with structlog.contextvars.bound_contextvars(
        joined_link_id=due.joined_link_id, meeting_id=due.meeting_id, tg_user_id=due.tg_user_id
    ):
        async with db.begin_write(api) as session:
            # Re-check under the fresh transaction: the link may have been flagged or its
            # meeting rescheduled out of the window since the unlocked sweep nominated it.
            link = (await session.exec(USERS_TO_NOTIFY_STATEMENT.where(JoinedUsers.id == due.joined_link_id))).first()
            if link is None:
                log.info("Skip starting-soon notification", reason=await skip_reason(session, due.joined_link_id))
                return False

            await api.send_message_to_user(link.user, starting_soon_view(link))
            link.notification_sent = True
            log.info(
                "Send starting-soon notification",
                lang=link.user.lang,
                lead_time_minutes=due.lead_time_minutes,
                meeting_datetime=due.meeting_datetime,
                outcome="enqueued",
            )
            return True


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Send a notification to all users that have joined a meeting that is about to start.

    Each link commits in its own transaction: one participant's failure cannot roll back
    the notifications already committed for the others, and a crash mid-sweep resumes
    cleanly on the next run.
    """
    nominated = await due_links()

    sent = 0
    skipped = 0
    failed = 0
    for due in nominated:
        try:
            if await notify_joined_link(due, api):
                sent += 1
            else:
                skipped += 1
        except Exception as error:
            failed += 1
            log.exception(
                "Failed to send starting-soon notification",
                joined_link_id=due.joined_link_id,
                meeting_id=due.meeting_id,
                tg_user_id=due.tg_user_id,
                reason="send_failed",
                error_type=error_type_name(error),
                exc_info=error,
            )

    log.info(
        "Starting-soon notification sweep complete",
        nominated=len(nominated),
        sent=sent,
        skipped=skipped,
        failed=failed,
    )

    if failed:
        raise RuntimeError(f"Failed to send notification to {failed} users. Check logs for more details.")
