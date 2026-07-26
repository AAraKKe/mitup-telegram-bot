"""Limit predicates over live `User`/`Meetup` state, resolved through the supporter-tier policy.

These helpers bridge a user/meeting to the `mitup_bot.supporter` policy: they read the owner's
`supporter_level` and ask the policy for the cap, then combine it with runtime state (how many active
meetings, the picked date, the requested capacity). The level -> cap mapping and the tier ordering
live in `supporter`; nothing here compares levels itself. A `None` cap from the policy means the tier
is uncapped for that dimension (Organizer everywhere, Patron for participant capacity).
"""

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot import supporter

if TYPE_CHECKING:
    from mitup_bot.models import User

MEETING_MAX_DURATION = dt.timedelta(days=7)

# Ceiling for the owner's `Settings.timeout`, the delay between a meeting's end and its
# deactivation. An hour is enough for the common case; a day is allowed so a meeting can stay up
# the day after it finished. Without a ceiling a timeout keeps its owner's meetings active forever,
# so they never expire and are never cleaned up.
MEETING_MAX_TIMEOUT_MINUTES = 24 * 60


def active_meetings_cap(user: User) -> int | None:
    """Maximum active meetings the user may own, or None (unlimited) for the Organizer tier."""
    return supporter.active_meetings_cap(user.supporter_level)


def scheduling_horizon_days(user: User) -> int | None:
    """How many days ahead the user may schedule, or None (unlimited) for the Organizer tier."""
    return supporter.scheduling_horizon_days(user.supporter_level)


def participant_capacity(user: User) -> int | None:
    """The participant cap on meetings this user owns, or None (uncapped) for Patron/Organizer."""
    return supporter.participant_capacity(user.supporter_level)


def effective_participant_capacity(owner: User, max_members: int | None) -> int | None:
    """The capacity actually enforced on a meeting: the owner's explicit `max_members` tightened by
    the owner's cap, or None (unlimited) which is only reachable for uncapped owners.

    A capped owner's `None` resolves to the cap and a limit above the cap is clamped to it, so a
    capped-owned meeting never behaves as unlimited. Grandfathered meetings already above the cap
    keep their participants; they simply read as full until they drop back under it.
    """
    cap = participant_capacity(owner)
    if cap is None:
        return max_members
    return cap if max_members is None else min(max_members, cap)


def at_active_meetings_cap(user: User) -> bool:
    """Whether the user already owns their cap's worth of active meetings.

    An uncapped (Organizer) owner is never at the cap. Counts only active meetings, so grandfathered
    over-cap users can still edit and leave their existing meetings; they are simply blocked from
    adding more until they drop below the cap.
    """
    cap = active_meetings_cap(user)
    if cap is None:
        return False
    active = sum(1 for meeting in user.meetups if meeting.active)
    return active >= cap


def within_scheduling_horizon(user: User, when: dt.datetime) -> bool:
    """Whether `when` falls on or before the user's furthest schedulable date.

    An uncapped (Organizer) horizon admits any date. The picked date is compared in the user's
    timezone, and the horizon boundary itself is allowed. A naive datetime is read as UTC, matching
    how meeting datetimes are persisted.
    """
    days = scheduling_horizon_days(user)
    if days is None:
        return True
    aware = when if when.tzinfo else when.replace(tzinfo=dt.UTC)
    picked_date = user.datetime_in_tz(aware).date()
    latest = user.now_in_tz().date() + dt.timedelta(days=days)
    return picked_date <= latest


def within_max_duration(start: dt.datetime, end: dt.datetime) -> bool:
    """Whether the span from `start` to `end` is at most `MEETING_MAX_DURATION`.

    No tier lifts this cap, so it takes no `User`. Naive datetimes are read as UTC, matching how
    meeting datetimes are persisted, so the delta is measured on comparable aware values.
    """
    start_utc = start if start.tzinfo else start.replace(tzinfo=dt.UTC)
    end_utc = end if end.tzinfo else end.replace(tzinfo=dt.UTC)
    return end_utc - start_utc <= MEETING_MAX_DURATION
