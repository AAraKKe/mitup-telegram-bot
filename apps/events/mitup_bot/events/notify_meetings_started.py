import datetime as dt
from dataclasses import dataclass

import structlog
from sqlmodel import and_, col, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

from .telemetry import error_type_name

log = structlog.get_logger(__name__)

MEETINGS_TO_NOTIFY_STARTED_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.active == true(),
        Meetup.datetime != null(),
        Meetup.datetime <= func.now(),
        Meetup.started_notification_sent == false(),
    )
)


@dataclass(frozen=True, slots=True)
class ParticipantSplit:
    """A meeting's roster partitioned into the started notification's recipients and the two
    deliberate exclusions, so both are countable and both can be recorded per user."""

    notify: list[JoinedUsers]
    waiting_list: list[JoinedUsers]
    inactive: list[JoinedUsers]


def minutes_late(meeting_datetime: dt.datetime | None) -> int | None:
    """How far past its start time a meeting is, `None` when it carries no start time.

    Coerces a naive timestamp (as read back from the DB) to UTC so the subtraction never mixes
    aware and naive datetimes. A healthy runner nominates seconds late; a backlogged one, hours.
    """
    if meeting_datetime is None:
        return None
    if meeting_datetime.tzinfo is None:
        meeting_datetime = meeting_datetime.replace(tzinfo=dt.UTC)
    return round((dt.datetime.now(dt.UTC) - meeting_datetime).total_seconds() / 60)


def split_participants(meeting: Meetup) -> ParticipantSplit:
    """Partition the roster: waiting-list members and non-MEMBER users are excluded, in that order
    of precedence — a waiting-list place is the reason they are skipped regardless of status."""
    notify: list[JoinedUsers] = []
    waiting_list: list[JoinedUsers] = []
    inactive: list[JoinedUsers] = []
    for link in meeting.joined_links:
        if link.is_waiting_list:
            waiting_list.append(link)
            continue
        if link.user.status is not UserStatus.MEMBER:
            inactive.append(link)
            continue
        notify.append(link)
    return ParticipantSplit(notify=notify, waiting_list=waiting_list, inactive=inactive)


@db.with_session
async def due_meeting_ids(session: AsyncSession) -> list[int]:
    """Nominate the meetings whose started notifications are currently due.

    Ids only, in a short read-only transaction: each meeting is processed later in its own
    write lifecycle, which re-checks that the notification is still due.
    """
    meetings = (await session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT)).all()
    for meeting in meetings:
        log.info(
            "Nominate meeting for a started notification",
            meeting_id=meeting.db_id,
            meeting_datetime=meeting.datetime,
            minutes_late=minutes_late(meeting.datetime),
            reason="start_time_reached",
        )
    return [meeting.db_id for meeting in meetings]


async def skip_reason(session: AsyncSession, meetup_id: int) -> str:
    """Name which condition of the nomination predicate stopped holding, by re-reading the row
    outside the predicate and testing each condition in turn.

    A future start time is the residual: it is the only condition evaluated against the database
    clock, so it is reported once everything readable off the row still holds.
    """
    meeting = (await session.exec(select(Meetup).where(col(Meetup.id) == meetup_id))).first()
    if meeting is None:
        return "meeting_deleted"
    if not meeting.active:
        return "meeting_deactivated"
    if meeting.started_notification_sent:
        return "already_notified"
    if meeting.datetime is None:
        return "datetime_cleared"
    return "rescheduled_to_future"


async def notify_participants(api: TelegramApiWrapper, meeting: Meetup, split: ParticipantSplit):
    """Enqueue one started notification per eligible participant and record both exclusions.

    The exclusions are user-visible (someone on the waiting list is deliberately not told the
    meeting began), so each is named per user rather than only counted on the summary line.
    """
    for link in split.waiting_list:
        log.info("Skip participant for a started notification", tg_user_id=link.user.tg_user_id, reason="waiting_list")
    for link in split.inactive:
        log.info(
            "Skip participant for a started notification", tg_user_id=link.user.tg_user_id, reason="user_not_member"
        )
    for link in split.notify:
        view = MitupView(
            description=NotificationMessages.STARTED.get(lang=link.user.lang, meeting_title=rich_title(meeting)),
            keyboard=[],
        )
        await api.send_message_to_user(link.user, view)
        log.info("Send started notification", tg_user_id=link.user.tg_user_id, lang=link.user.lang, outcome="enqueued")


async def notify_meeting_started(meetup_id: int, api: TelegramApiWrapper) -> int | None:
    """Notify one meeting's participants and flag it notified; returns the number of
    participants notified, or `None` when the meeting is no longer due.

    `None` rather than 0 because a meeting that was skipped and a meeting that was flagged and
    edited but had nobody eligible to tell are different outcomes, and one counter reporting both
    is what makes a run that touched 30 meetings and reached 0 people read as healthy.

    The write lifecycle enqueues the notifications and message edits inside the
    transaction and drains them only after `started_notification_sent` committed: no
    transaction is held across Telegram I/O, and a crash mid-sweep cannot re-notify this
    meeting on the next run. Unreachable participants are marked inactive by the
    lifecycle's reconcile step.
    """
    with structlog.contextvars.bound_contextvars(meeting_id=meetup_id):
        async with db.begin_write(api) as session:
            # Re-check under the fresh transaction: the meeting may have been deactivated or
            # flagged since the unlocked sweep nominated it.
            meeting = (await session.exec(MEETINGS_TO_NOTIFY_STARTED_STATEMENT.where(Meetup.id == meetup_id))).first()
            if meeting is None:
                log.info("Skip started notification", reason=await skip_reason(session, meetup_id))
                return None

            split = split_participants(meeting)
            await notify_participants(api, meeting, split)
            await api.update_meeting_messages(meeting=meeting)
            meeting.started_notification_sent = True
            log.info(
                "Notify meeting participants that it started",
                meeting_datetime=meeting.datetime,
                participants_notified=len(split.notify),
                waiting_list_skipped=len(split.waiting_list),
                inactive_skipped=len(split.inactive),
                messages_updated=True,
                outcome="notified",
                reason="eligible_participants_notified" if split.notify else "no_eligible_participants",
            )
            return len(split.notify)


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Send a notification to all participants when a meeting's scheduled time has arrived.

    Each meeting commits in its own transaction: one meeting's failure cannot roll back the
    notifications already committed for the others, and a crash mid-sweep resumes cleanly
    on the next run.
    """
    meeting_ids = await due_meeting_ids()

    sent = 0
    notified_meetings = 0
    skipped = 0
    failed = 0

    for meetup_id in meeting_ids:
        try:
            participants = await notify_meeting_started(meetup_id, api)
        except Exception as error:
            failed += 1
            log.exception(
                "Failed to process started notification for meeting",
                meeting_id=meetup_id,
                reason="started_notification_failed",
                error_type=error_type_name(error),
                exc_info=error,
            )
            continue
        if participants is None:
            skipped += 1
            continue
        sent += participants
        notified_meetings += 1

    log.info(
        "Started notification sweep complete",
        nominated=len(meeting_ids),
        meetings_notified=notified_meetings,
        participants_notified=sent,
        skipped=skipped,
        failed=failed,
    )

    if failed:
        raise RuntimeError(f"Failed to process started notifications for {failed} meetings. Check logs for details.")
