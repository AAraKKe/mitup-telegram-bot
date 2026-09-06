"""The datetime conventions every layer shares.

Meeting datetimes and every timestamp column are persisted as UTC in columns that carry no
timezone, so a value read back from the database is naive and means UTC. These helpers are the one
place that convention is applied: `as_utc` tags a bare value so it can be compared or subtracted,
`in_timezone` reads one as a wall clock somewhere, and `local_to_utc` turns a wall clock someone
typed back into the instant that gets stored. Writing a moment out for a reader is a separate
concern and lives in `mitup_bot.views.datetime_format`, which formats what these return.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_TIMEZONE = ZoneInfo("UTC")


class DateFormat(StrEnum):
    """How the date half of a moment is written."""

    DEFAULT = "default"
    LONG = "long"
    FULL = "full"


@dataclass(frozen=True)
class TimeFormat:
    """The three settings deciding how a meeting's moments are written out."""

    show_timezone: bool
    clock_24h: bool
    date_format: DateFormat


def as_utc(value: dt.datetime) -> dt.datetime:
    """*value* as an aware datetime, reading a naive one as UTC the way datetimes are stored.

    An already-aware value keeps the offset it arrived with: it names the same instant either way,
    which is all a comparison or a subtraction asks of it. Use `in_timezone(value, dt.UTC)` when the
    UTC wall clock itself is what matters, as it does when the value is written out.
    """
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def in_timezone(value: dt.datetime, tz: dt.tzinfo) -> dt.datetime:
    """*value* as a wall clock in *tz*, reading a naive value as UTC."""
    return as_utc(value).astimezone(tz)


def local_to_utc(date: dt.date, time: dt.time, tz: dt.tzinfo) -> dt.datetime:
    """The instant at which the clocks in *tz* read *time* on *date*.

    Any timezone already carried by *time* is replaced by *tz*, so a caller holding a bare wall
    clock and a caller holding one it already tagged both land on the same instant.
    """
    return dt.datetime.combine(date, time.replace(tzinfo=tz)).astimezone(dt.UTC)


def parse_timezone(name: str) -> ZoneInfo | None:
    """The zone *name* identifies, or None when it names no zone this system knows.

    The fallback is left to the caller: what to record about an unusable zone id depends on where
    the id came from.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None
