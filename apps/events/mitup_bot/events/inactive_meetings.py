import datetime as dt
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, assert_never, cast

import structlog
from sqlalchemy import ColumnElement
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, case, col, delete, exists, func, literal, null, or_, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel

from .lifecycle_queries import owner_tier_window_elapsed, resolved_windows, sql_interval
from .telemetry import emit_per_supporter_level, error_type_name

log = structlog.get_logger(__name__)

LEFT_OWNER_DATELESS_LIFETIME = LifecyclePolicy.get().left_owner_dateless_lifetime
DATELESS_WINDOWS = resolved_windows(lambda policy: policy.dateless_lifetime)


class DeactivationReason(StrEnum):
    """Which arm of the deactivation predicate made a meeting due."""

    DATELESS_WINDOW_ELAPSED = "dateless_window_elapsed"
    LEFT_OWNER_DATELESS_WINDOW_ELAPSED = "left_owner_dateless_window_elapsed"
    PAST_END_DATETIME_PLUS_OWNER_TIMEOUT = "past_end_datetime_plus_owner_timeout"


class SkipReason(StrEnum):
    """Why a nominated meeting was left active once its row lock was finally taken."""

    MEETING_DELETED = "meeting_deleted"
    ALREADY_INACTIVE = "already_inactive"
    RESCHEDULED = "rescheduled"
    OWNER_REJOINED = "owner_rejoined"
    TIER_WINDOW_EXTENDED = "tier_window_extended"
    PREDICATE_NO_LONGER_MATCHES = "predicate_no_longer_matches"


DATELESS_WINDOW_ELAPSED = and_(
    Meetup.datetime == null(),
    owner_tier_window_elapsed(col(Meetup.activated_time), lambda policy: policy.dateless_lifetime),
)

LEFT_OWNER_WINDOW_ELAPSED = and_(
    Meetup.datetime == null(),
    User.status == UserStatus.LEFT,
    col(Meetup.activated_time) + sql_interval(LEFT_OWNER_DATELESS_LIFETIME) < func.now(),
)

PAST_END_DATETIME_PLUS_OWNER_TIMEOUT = and_(
    Meetup.datetime != null(),
    func.now()
    > func.coalesce(Meetup.end_datetime, Meetup.datetime)
    + func.cast(func.concat(Settings.timeout, " minutes"), INTERVAL),
)

# The active meetings due for deactivation: dateless past the owner's tier window, dateless with a
# LEFT owner past the shorter window, or dated past its end plus the owner's timeout.
DUE_FOR_DEACTIVATION = and_(
    Meetup.active == true(),
    or_(DATELESS_WINDOW_ELAPSED, LEFT_OWNER_WINDOW_ELAPSED, PAST_END_DATETIME_PLUS_OWNER_TIMEOUT),
)

# Named in the predicate's own branch order, so a dateless meeting whose owner also left is
# attributed to the tier window that would have deactivated it either way.
DEACTIVATION_REASON: ColumnElement[str] = case(
    (DATELESS_WINDOW_ELAPSED, literal(DeactivationReason.DATELESS_WINDOW_ELAPSED.value)),
    (LEFT_OWNER_WINDOW_ELAPSED, literal(DeactivationReason.LEFT_OWNER_DATELESS_WINDOW_ELAPSED.value)),
    else_=literal(DeactivationReason.PAST_END_DATETIME_PLUS_OWNER_TIMEOUT.value),
)

MEETINGS_TO_DEACTIVATE_STATEMENT: SelectOfScalar[Meetup] = (
    select(Meetup).join(User).join(Settings).where(DUE_FOR_DEACTIVATION)
)

DUE_MEETING_FACTS_STATEMENT = (
    select(col(Meetup.id), col(User.tg_user_id), col(User.supporter_level), DEACTIVATION_REASON)
    .select_from(Meetup)
    .join(User)
    .join(Settings)
    .where(DUE_FOR_DEACTIVATION)
)

JOINED_ONLY_WITHOUT_ACTIVE_LINKS_STATEMENT: SelectOfScalar[int] | SelectOfScalar[None] = select(User.id).where(
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
"""Selects the ids of JOINED_ONLY users who have no remaining active-meeting links.

These users were only ever reachable through the inline-join flow and have no further meetings to
attend, so retaining them wastes space and pollutes user-count metrics. Ids rather than a bare
`DELETE`: nothing downstream can name a hard-deleted account, so the purge records who it took.
"""


@dataclass(frozen=True, slots=True)
class DueMeeting:
    """A meeting nominated for deactivation, with the facts that made it due.

    The reason comes from the database, the only place that knows which arm of the disjunction
    matched. Re-deriving it in Python afterwards is not equivalent: the owner's tier and status,
    their timeout and the meeting's datetime can all change before anyone looks.
    """

    meeting_id: int
    owner_tg_user_id: int
    owner_supporter_level: SupporterLevel
    reason: DeactivationReason

    @property
    def window_days(self) -> int | None:
        """The window, in whole days, the branch that nominated this meeting applied to its owner.

        The dateless windows are generated from the same `levels_by_duration` grouping the SQL
        branches are, and keyed by the level the same row carries, so this is the lookup that
        branch performed rather than a mirror of it. The dated branch is timed by the owner's
        `Settings.timeout` instead of a lifecycle window, so it carries none rather than a
        misleading one.
        """
        match self.reason:
            case DeactivationReason.DATELESS_WINDOW_ELAPSED:
                return DATELESS_WINDOWS[self.owner_supporter_level]
            case DeactivationReason.LEFT_OWNER_DATELESS_WINDOW_ELAPSED:
                return LifecyclePolicy.interval_days(LEFT_OWNER_DATELESS_LIFETIME)
            case DeactivationReason.PAST_END_DATETIME_PLUS_OWNER_TIMEOUT:
                return None
            case _ as unreachable:
                assert_never(unreachable)

    @property
    def log_fields(self) -> dict[str, Any]:
        """The identity and tier facts every line about this meeting carries."""
        return {
            "meeting_id": self.meeting_id,
            "owner_tg_user_id": self.owner_tg_user_id,
            "supporter_level": self.owner_supporter_level.value,
            "window_days": self.window_days,
        }


def tier_window_days(owner: User) -> int:
    """The dateless window the owner's *current* tier runs on, in whole days."""
    return LifecyclePolicy.interval_days(LifecyclePolicy.get(owner.supporter_level).dateless_lifetime)


def skip_reason(due: DueMeeting, meeting: Meetup) -> SkipReason:
    """Name what stopped holding between the sweep's read and the row lock.

    Only what the locked row proves is named: a reactivation, a lengthened timeout and a re-stamped
    activation all leave the same trace, so they share the residual reason.
    """
    left_owner_window = DeactivationReason.LEFT_OWNER_DATELESS_WINDOW_ELAPSED
    if not meeting.active:
        return SkipReason.ALREADY_INACTIVE
    if meeting.datetime is not None and due.reason is not DeactivationReason.PAST_END_DATETIME_PLUS_OWNER_TIMEOUT:
        return SkipReason.RESCHEDULED
    if due.reason is left_owner_window and meeting.owner.status is not UserStatus.LEFT:
        return SkipReason.OWNER_REJOINED
    if (
        due.reason is DeactivationReason.DATELESS_WINDOW_ELAPSED
        and due.window_days is not None
        and tier_window_days(meeting.owner) > due.window_days
    ):
        return SkipReason.TIER_WINDOW_EXTENDED
    return SkipReason.PREDICATE_NO_LONGER_MATCHES


def log_skip(due: DueMeeting, reason: SkipReason):
    """Record a nominated meeting the sweep left active, naming both the window that nominated it
    and what stopped holding — a silent early return is counted in nominations, in neither
    deactivated nor failed, and absent from the log."""
    log.info("Skip meeting deactivation", **due.log_fields, nominated_reason=due.reason.value, reason=reason.value)


@db.with_session
async def due_meetings(session: AsyncSession) -> list[DueMeeting]:
    """Collect the meetings currently due for deactivation, each labelled with the branch and the
    window that nominated it.

    Facts only, in a short read-only transaction: every decision about a meeting is made later
    under that meeting's row lock, so nothing read here may feed a mutation.
    """
    rows = (await session.exec(DUE_MEETING_FACTS_STATEMENT)).all()
    return [
        DueMeeting(
            meeting_id=cast(int, meeting_id),
            owner_tg_user_id=owner_tg_user_id,
            owner_supporter_level=SupporterLevel(supporter_level),
            reason=DeactivationReason(reason),
        )
        for meeting_id, owner_tg_user_id, supporter_level, reason in rows
    ]


async def clear_meeting_data(session: AsyncSession, meeting: Meetup) -> dict[str, Any]:
    """Empty a deactivated meeting and report what that destroyed.

    The membership belongs to the run that just ended. Reactivation brings the meeting back as a
    fresh one, so it must start empty and everybody joins again instead of being re-enrolled into a
    meeting they only ever attended once. The waiting list rides on these same rows and goes with
    them, the invited users exist only in the context of this meeting, and the message cards are
    left untracked because the meeting is now over. After the deletes nothing can reconstruct who
    was in the meeting, which is why the counts and the invited ids are captured here.
    """
    invited_user_ids = [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]
    invited_users_deleted = 0
    if invited_user_ids:
        result = await session.exec(delete(User).where(col(User.id).in_(invited_user_ids)))
        invited_users_deleted = result.rowcount or 0

    participants = await session.exec(delete(JoinedUsers).where(col(JoinedUsers.meetup_id) == meeting.db_id))
    messages = await session.exec(delete(Message).where(col(Message.meetup_id) == meeting.db_id))
    return {
        "invited_user_ids": invited_user_ids,
        "invited_users_deleted": invited_users_deleted,
        "participants_removed": participants.rowcount or 0,
        "messages_deleted": messages.rowcount or 0,
    }


async def deactivate_meeting_locked(session: AsyncSession, due: DueMeeting, api: TelegramApiWrapper) -> bool:
    """Deactivate one meeting under its row lock; returns False when it is no longer due.

    The unlocked sweep only nominated this meeting: the eligibility decision and the
    invited-user cleanup must be re-derived from what the locked load returns, or the job
    races the live bot — a concurrent invite leaks its invited-user row, and a concurrent
    reschedule gets deactivated on stale timing.
    """
    meeting = await Meetup.by_id(session, due.meeting_id, for_update=True)
    if meeting is None:
        log_skip(due, SkipReason.MEETING_DELETED)
        return False

    # Re-check the deactivation predicate under the lock: the owner may have rescheduled the
    # meeting between the sweep's read and here. We hold the row lock, so this read is final.
    still_due = (await session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT.where(Meetup.id == due.meeting_id))).first()
    if still_due is None:
        log_skip(due, skip_reason(due, meeting))
        return False

    # Enqueued under write mode: the payload is rendered now, inside the transaction, but the
    # edits execute only after commit — the row lock is never held across Telegram I/O.
    await api.update_meeting_messages(meeting=meeting, has_finished=True)

    meeting.active = False
    meeting.expiration_time = dt.datetime.now(dt.UTC)
    cleared = await clear_meeting_data(session, meeting)

    log.info(
        "Deactivate meeting",
        **due.log_fields,
        reason=due.reason.value,
        owner_status=meeting.owner.status.value,
        meeting_datetime=meeting.datetime,
        end_datetime=meeting.end_datetime,
        activated_time=meeting.activated_time,
        expiration_time=meeting.expiration_time,
        **cleared,
    )
    return True


async def deactivate_meeting(due: DueMeeting, api: TelegramApiWrapper) -> bool:
    """One meeting's deactivation in its own write lifecycle: commit (releasing the row
    lock) before the queued fan-out drains. The bare critical section stays importable for
    the row-lock race tests in tests/data/db_behavior/.

    The meeting id is bound for the whole body so the api-wrapper and reconcile lines emitted by
    the post-commit drain name the meeting they belong to.
    """
    with structlog.contextvars.bound_contextvars(meeting_id=due.meeting_id):
        async with db.begin_write(api) as session:
            return await deactivate_meeting_locked(session, due, api)


@db.with_session
async def delete_joined_only_users(session: AsyncSession) -> int:
    """Purge the JOINED_ONLY users with no remaining active-meeting links; returns how many."""
    user_ids = list((await session.exec(JOINED_ONLY_WITHOUT_ACTIVE_LINKS_STATEMENT)).all())
    if user_ids:
        await session.exec(delete(User).where(col(User.id).in_(user_ids)))

    log.info(
        "Delete joined-only users without active links",
        user_ids=user_ids,
        count=len(user_ids),
        reason="joined_only_without_active_meeting_links",
    )
    return len(user_ids)


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Mark meetings as inactive when they've been finished for longer than the configured timeout.

    Each meeting commits in its own transaction under its row lock: locks are held per meeting
    (never across the sweep or across Telegram I/O), and a crash mid-sweep keeps every
    deactivation already committed — the remaining meetings are still due on the next run.
    """
    nominated = await due_meetings()
    metrics.emit(MetricKey.MEETINGS_TO_DEACTIVATE, len(nominated), MetricUnit.COUNT)

    deactivated: list[DueMeeting] = []
    skipped = 0
    failed = 0

    for due in nominated:
        try:
            if await deactivate_meeting(due, api):
                deactivated.append(due)
            else:
                skipped += 1
        except Exception as error:
            failed += 1
            log.exception(
                "Failed to deactivate meeting",
                meeting_id=due.meeting_id,
                reason="deactivation_failed",
                error_type=error_type_name(error),
                exc_info=error,
            )

    joined_only_deleted = await delete_joined_only_users()

    emit_per_supporter_level(
        metrics, MetricKey.MEETINGS_DEACTIVATED, Counter(due.owner_supporter_level for due in deactivated)
    )
    metrics.emit(MetricKey.MEETINGS_DEACTIVATION_FAILED, failed, MetricUnit.COUNT)
    metrics.emit(MetricKey.JOINED_ONLY_USERS_DELETED, joined_only_deleted, MetricUnit.COUNT)

    log.info(
        "Deactivation sweep complete",
        nominated=len(nominated),
        deactivated=len(deactivated),
        skipped=skipped,
        failed=failed,
        joined_only_users_deleted=joined_only_deleted,
        reasons=dict(Counter(due.reason.value for due in deactivated)),
        windows=dict(Counter(due.window_days for due in deactivated)),
    )

    if failed:
        raise RuntimeError(f"Failed to deactivate {failed} meetings. Check logs for details.")
