"""The datetime conventions every layer shares.

Every moment is an aware datetime and is stored as UTC. `local_to_utc` turns a wall clock someone
typed into the instant that gets stored; writing a moment out for a reader lives in
`mitup_bot.views.datetime_format`.
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
