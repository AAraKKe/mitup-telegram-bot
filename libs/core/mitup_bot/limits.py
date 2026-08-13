"""Limit predicates over live `User`/`Meetup` state, resolved through the supporter-tier policy.

These helpers bridge a user/meeting to the `mitup_bot.supporter` policy: they read the owner's
`supporter_level` and ask the policy for the cap, then combine it with runtime state (how many active
meetings, the picked date, the requested capacity). The level -> cap mapping and the tier ordering
live in `supporter`; nothing here compares levels itself. A `None` cap from the policy means the tier
is uncapped for that dimension (Organizer everywhere, Patron for participant capacity).

The character caps on a meeting's free-text fields also live here, each paired with the length the
meeting card guarantees that field on screen: between the two a field is wrappable, and the card
ellipsizes it rather than giving up a field with no room left to trade. No tier lifts either number,
so they are plain constants rather than policy lookups.
"""

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot import supporter
from mitup_bot.lifecycle import LifecyclePolicy

if TYPE_CHECKING:
    from mitup_bot.models import User


TITLE_MAX_CHARS = 200
"""Longest meeting title accepted from its owner.

A meeting card renders every field into a single Telegram message, which the Bot API caps at 4096
characters. The title also heads every list row, notification and inline result, so it is held far
tighter than the card budget alone would require.
"""

DESCRIPTION_MAX_CHARS = 3500
"""Longest meeting description accepted from its owner.

Deliberately generous: a description may legitimately take up most of the card, which renders into a
single Telegram message capped at 4096 characters. A description this long combined with the other
fields is left to the card's own render budget rather than refused here.
"""

DESCRIPTION_GUARANTEED_CHARS = 500
"""Description length a meeting card never renders below.

The description is the first thing an over-budget card gives up, because it is the only field long
enough to pay for the overrun on its own. This floor is what stops that from emptying it: enough
prose to still say what the meeting is. Measured in UTF-16 code units, as the card measures
everything.
"""

LOCATION_NAME_MAX_CHARS = 256
"""Longest place name accepted for a meeting's location.

The venue is one line of the card, and the card renders into a single Telegram message capped at
4096 characters.
"""

LOCATION_NAME_GUARANTEED_CHARS = 128
"""Place-name length a meeting card never renders below.

A card that is still over budget with its description at the floor above ellipsizes the place name
next, down to this: enough to leave the venue recognizable to someone who has to find it. Measured
in UTF-16 code units, as the card measures everything.
"""


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
    """Whether the span from `start` to `end` is at most `LifecyclePolicy.get().max_duration`.

    No tier lifts this cap, so it takes no `User`. Naive datetimes are read as UTC, matching how
    meeting datetimes are persisted, so the delta is measured on comparable aware values.
    """
    start_utc = start if start.tzinfo else start.replace(tzinfo=dt.UTC)
    end_utc = end if end.tzinfo else end.replace(tzinfo=dt.UTC)
    return end_utc - start_utc <= LifecyclePolicy.get().max_duration
