import datetime as dt
from typing import cast

import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, delete, exists, func, literal, null, or_, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

log = structlog.get_logger(__name__)

# The amount of time a meeting stays active after it has been created when there is no datetime set
INTERVAL_TO_DEACTIVATE = "1 year"

# Query to get all meetings to be deactivated
#   - The meeting is currently active
#   - The meeting has a datetime set
#   - The current time is past meeting.datetime + timeout from the owner's settings
#
# If the meeting does not have a datetime set, the meeting is deactivated INTERVAL_TO_DEACTIVATE from the creation date.
# When end_datetime is set, the meeting window extends to end_datetime + timeout.
# When only datetime is set, the meeting is deactivated after datetime + timeout.
# When no datetime is set, fall back to created_time + INTERVAL_TO_DEACTIVATE.
MEETINGS_TO_DEACTIVATE_STATEMENT: SelectOfScalar[Meetup] = (
    select(Meetup)
    .join(User)
    .join(Settings)
    .where(
        and_(
            Meetup.active == true(),
            or_(
                and_(
                    Meetup.datetime == null(),
                    Meetup.created_time + func.cast(literal(INTERVAL_TO_DEACTIVATE), INTERVAL) < func.now(),
                ),
                and_(
                    Meetup.datetime != null(),
                    func.now()
                    > func.coalesce(Meetup.end_datetime, Meetup.datetime)
                    + func.cast(func.concat(Settings.timeout, " minutes"), INTERVAL),
                ),
            ),
        )
    )
)


@db.with_session
async def due_meeting_ids(session: AsyncSession) -> list[int]:
    """Collect the ids of the meetings currently due for deactivation.

    Ids only, in a short read-only transaction: every decision about a meeting is made later
    under that meeting's row lock, so nothing read here may feed a mutation.
    """
    return [meeting.db_id for meeting in (await session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT)).all()]


async def deactivate_meeting_locked(session: AsyncSession, meetup_id: int, api: TelegramApiWrapper) -> bool:
    """Deactivate one meeting under its row lock; returns False when it is no longer due.

    The unlocked sweep only nominated this meeting: the eligibility decision and the
    invited-user cleanup must be re-derived from what the locked load returns, or the job
    races the live bot — a concurrent invite leaks its invited-user row, and a concurrent
    reschedule gets deactivated on stale timing.
    """
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    if meeting is None:
        return False

    # Re-check the deactivation predicate under the lock: the owner may have rescheduled the
    # meeting between the sweep's read and here. We hold the row lock, so this read is final.
    still_due = (await session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT.where(Meetup.id == meetup_id))).first()
    if still_due is None:
        log.info("Meeting no longer due for deactivation, skipping", meeting=meetup_id)
        return False

    # Enqueued under write mode: the payload is rendered now, inside the transaction, but the
    # edits execute only after commit — the row lock is never held across Telegram I/O.
    await api.update_meeting_messages(meeting=meeting, has_finished=True)

    meeting.active = False
    meeting.expiration_time = dt.datetime.now(dt.UTC)

    # Delete all users that were added to the meeting that were invited.
    # These users exist only in the context of the current meeting.
    invited_users_ids = [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]
    if invited_users_ids:
        await session.exec(delete(User).where(col(User.id).in_(invited_users_ids)))

    # Same with messages, any message attached to this meeting is left untracked as the
    # meeting is now considered over
    await session.exec(delete(Message).where(col(Message.meetup_id) == meetup_id))
    return True


async def deactivate_meeting(meetup_id: int, api: TelegramApiWrapper) -> bool:
    """One meeting's deactivation in its own write lifecycle: commit (releasing the row
    lock) before the queued fan-out drains. The bare critical section stays importable for
    the row-lock race tests in tests/models/db_behavior/."""
    async with db.begin_write(api) as session:
        return await deactivate_meeting_locked(session, meetup_id, api)


@db.with_session
async def delete_joined_only_users(session: AsyncSession) -> int:
    """Delete JOINED_ONLY users who have no remaining active-meeting links.

    These users were only ever reachable through the inline-join flow and have no further
    meetings to attend, so retaining them wastes space and pollutes user-count metrics.
    """
    stmt = delete(User).where(
        and_(
            User.status == UserStatus.JOINED_ONLY,
            ~exists(
                select(1)
                .select_from(JoinedUsers)
                .join(Meetup)
                .where(and_(JoinedUsers.user_id == User.id, Meetup.active == true()))
            ),
        )
    )
    result = await session.exec(stmt)
    return result.rowcount or 0


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Mark meetings as inactive when they've been finished for longer than the configured timeout.

    Each meeting commits in its own transaction under its row lock: locks are held per meeting
    (never across the sweep or across Telegram I/O), and a crash mid-sweep keeps every
    deactivation already committed — the remaining meetings are still due on the next run.
    """
    meeting_ids = await due_meeting_ids()
    metrics.emit(MetricKey.MEETINGS_TO_DEACTIVATE, len(meeting_ids), MetricUnit.COUNT)

    deactivated = 0
    failed = 0
    failed_details: list[str] = []

    for meetup_id in meeting_ids:
        try:
            if await deactivate_meeting(meetup_id, api):
                deactivated += 1
        except Exception as e:
            failed += 1
            log.exception("Failed to deactivate meeting", meeting=meetup_id, exc_info=e)
            failed_details.append(f"Failed to deactivate meeting (meeting: {meetup_id}). Error: {e}.")

    joined_only_deleted = await delete_joined_only_users()

    metrics.emit(MetricKey.MEETINGS_DEACTIVATED, deactivated, MetricUnit.COUNT)
    metrics.emit(
        MetricKey.MEETINGS_DEACTIVATION_FAILED,
        failed,
        MetricUnit.COUNT,
        properties={"failed_details": failed_details} if failed_details else None,
    )
    metrics.emit(MetricKey.JOINED_ONLY_USERS_DELETED, joined_only_deleted, MetricUnit.COUNT)

    if failed:
        raise RuntimeError(
            f"Failed to deactivate {failed} meetings. Check individual failed_details for more information."
        )
