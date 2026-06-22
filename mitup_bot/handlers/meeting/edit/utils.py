import datetime as dt

from telegram import Message, MessageEntity
from telegram.ext import filters

from mitup_bot.custom_context import ContextId
from mitup_bot.models import Meetup
from mitup_bot.utils.mitup_types import TMitupContext


def to_utc(value: dt.datetime) -> dt.datetime:
    """Normalise a possibly-naive datetime to aware UTC.

    Meeting datetimes are stored as UTC but may be persisted naive, so a naive value is
    tagged as UTC rather than reinterpreted; mirrors ``Meetup.enforce_datetime_ordering``.
    """
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def is_in_past(candidate: dt.datetime, meeting: Meetup) -> bool:
    """True if candidate is at or before the meeting owner's current time (both compared in UTC)."""
    return to_utc(candidate) <= meeting.owner.now_in_tz().astimezone(dt.UTC)


class DateTimeEntityFilter(filters.MessageFilter):
    """Accept messages that contain at least one `date_time` entity."""

    def filter(self, message: Message) -> bool:
        return any(e.type == MessageEntity.DATE_TIME for e in (message.entities or []))


def safe_anchor_date(reference_datetime: dt.datetime | None, user_now: dt.datetime) -> dt.date:
    """Return the anchor date for a calendar view.

    If the reference datetime is set and in the future, use its date.
    Otherwise use the current date.
    """
    if reference_datetime:
        reference_date = dt.date(reference_datetime.year, reference_datetime.month, reference_datetime.day)
        return reference_date if reference_date >= user_now.date() else user_now.date()
    return user_now.date()


def cleanup_states(context: TMitupContext) -> None:
    context.clean_user_data(
        [
            ContextId.EDIT_MEETING_TITLE,
            ContextId.EDIT_MEETING_DESCRIPTION,
            ContextId.EDIT_MEETING_LOCATION_NAME,
            ContextId.EDIT_MEETING_LOCATION_COORDINATES,
            ContextId.EDIT_MEETING_TIME,
            ContextId.EDIT_MEETING_DURATION,
            ContextId.EDIT_MEETING_END_DATETIME,
        ]
    )
