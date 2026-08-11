import datetime as dt

import structlog

from mitup_bot import limits
from mitup_bot.models import Meetup
from mitup_bot.utils.messages import MeetingEditDateTimeMessages, MeetingEditDurationMessages

from ...utils import scheduling_horizon_rejection

# The When feature's domain layer: what a proposed start or end is allowed to be, and what setting
# one does to the meeting. Nothing here touches Telegram, so both halves' handlers and the tests can
# reach a verdict without an update in hand.

log = structlog.get_logger(__name__)


def to_utc(value: dt.datetime) -> dt.datetime:
    """Normalise a possibly-naive datetime to aware UTC.

    Meeting datetimes are stored as UTC but may be persisted naive, so a naive value is
    tagged as UTC rather than reinterpreted; mirrors ``Meetup.enforce_datetime_ordering``.
    """
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def is_in_past(candidate: dt.datetime, meeting: Meetup) -> bool:
    """True if candidate is at or before the meeting owner's current time (both compared in UTC)."""
    return to_utc(candidate) <= meeting.owner.now_in_tz().astimezone(dt.UTC)


def safe_anchor_date(reference_datetime: dt.datetime | None, user_now: dt.datetime) -> dt.date:
    """Return the anchor date for a calendar view.

    If the reference datetime is set and in the future, use its date.
    Otherwise use the current date.
    """
    if reference_datetime:
        reference_date = dt.date(reference_datetime.year, reference_datetime.month, reference_datetime.day)
        return reference_date if reference_date >= user_now.date() else user_now.date()
    return user_now.date()


def validate_start_datetime(start_dt: dt.datetime, meeting: Meetup, lang: str) -> str | None:
    """Return an error message string if start_dt is invalid, or None if valid.

    The scheduling horizon is not checked here: call sites check it separately so the rejection can
    carry the Collaborate upsell, and time-only edits skip it entirely so a grandfathered far-future
    meeting can still have its time adjusted.

    All four start-datetime paths reach this, and the owner only ever sees the message, so the
    rejection is recorded here. The owner's own clock sits next to what they proposed: whether a
    datetime is in the past is a statement about their timezone, and a timezone-boundary bug is
    otherwise indistinguishable from a user mistake.
    """
    if is_in_past(start_dt, meeting):
        log.info(
            "Meeting start datetime rejected",
            user_id=meeting.owner.db_id,
            reason="start_in_past",
            proposed_datetime=start_dt,
            owner_now=meeting.owner.now_in_tz(),
        )
        return MeetingEditDateTimeMessages.START_IN_PAST.get_text(lang=lang)
    return None


def validate_end_datetime(end_dt: dt.datetime, meeting: Meetup, lang: str) -> str | None:
    """Return an error message string if end_dt is invalid, or None if valid.

    Checks the past constraint before the ordering constraint: a past end time is
    the more fundamental problem (the meeting would be auto-deactivated), so it takes
    precedence in the error shown. The one-week duration cap applies to every tier and is
    checked last, once the end is known to be a future time after the start.

    The scheduling horizon is not checked here: call sites check it separately so the rejection can
    carry the Collaborate upsell.

    Four call sites share this decision and collapse its three causes into one message on screen,
    so the cause is recorded here alongside the start time it was judged against.
    """
    assert meeting.datetime is not None

    def reject(reason: str) -> None:
        log.info(
            "Meeting end datetime rejected",
            user_id=meeting.owner.db_id,
            reason=reason,
            proposed_end=end_dt,
            meeting_datetime=meeting.datetime,
        )

    if is_in_past(end_dt, meeting):
        reject("end_in_past")
        return MeetingEditDurationMessages.END_IN_PAST.get_text(lang=lang)
    if to_utc(end_dt) <= to_utc(meeting.datetime):
        reject("end_before_start")
        return MeetingEditDurationMessages.END_BEFORE_START.get_text(lang=lang)
    if not limits.within_max_duration(meeting.datetime, end_dt):
        reject("exceeds_max_duration")
        return MeetingEditDurationMessages.END_MAX_DURATION.get_text(lang=lang)
    return None


def start_beyond_horizon(meeting: Meetup, start_dt: dt.datetime) -> str | None:
    """The upsell body when start_dt is past the owner's scheduling horizon, else None."""
    return scheduling_horizon_rejection(meeting.owner, start_dt, field="start")


def end_beyond_horizon(meeting: Meetup, end_dt: dt.datetime) -> str | None:
    """The upsell body when end_dt is past the owner's scheduling horizon, else None."""
    return scheduling_horizon_rejection(meeting.owner, end_dt, field="end")


def apply_start_datetime(meeting: Meetup, start_dt: dt.datetime, *, input_source: str) -> bool:
    """Set the meeting's start time, reporting whether the end time was cleared with it.

    The single write point for `meeting.datetime` across all four input paths, so the one line it
    emits carries both the change the owner asked for and the two they did not: setting a start at
    or after the end makes `enforce_datetime_ordering` drop the end time *and* the lock-on-start
    rule that depended on it. Cause and effect land on the same record.
    """
    old_datetime = meeting.datetime
    previous_end = meeting.end_datetime
    previous_lock_on_start = meeting.lock_on_start

    meeting.datetime = start_dt
    end_cleared = meeting.enforce_datetime_ordering()

    log.info(
        "Meeting start datetime set",
        user_id=meeting.owner.db_id,
        old_datetime=old_datetime,
        new_datetime=meeting.datetime,
        input_source=input_source,
        end_datetime_cleared=end_cleared,
        previous_end_datetime=previous_end,
        lock_on_start_reset=end_cleared and previous_lock_on_start,
        timezone=str(meeting.timezone),
    )
    return end_cleared


def apply_end_datetime(meeting: Meetup, end_dt: dt.datetime, *, input_source: str):
    """Set the meeting's end time, naming where the value came from and the span it produces.

    The single write point for `meeting.end_datetime` across all three input paths. The resulting
    duration is on the line because the end time is what the deactivation sweep judges a finished
    meeting by, so "how long did the owner make it" is the question asked of this record later.
    """
    assert meeting.datetime is not None, "an end time is only editable once a start time exists"
    old_end_datetime = meeting.end_datetime
    meeting.end_datetime = end_dt

    log.info(
        "Meeting end datetime set",
        user_id=meeting.owner.db_id,
        old_end_datetime=old_end_datetime,
        new_end_datetime=end_dt,
        duration_minutes=int((to_utc(end_dt) - to_utc(meeting.datetime)).total_seconds() // 60),
        input_source=input_source,
    )
