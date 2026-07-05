"""Free-tier limit resolution, premium-aware.

Mirrors the configure-once module pattern of `db` and `timezone_api`: `MitupRuntime` calls
`configure` at startup with the merged `LimitsConfig`, and handlers read the resolved caps through
the helpers below. The holder defaults to the code defaults in `LimitsConfig`, so any entry point
that never calls `configure` (tests, one-off scripts) still resolves the shipped limits.
"""

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.config import LimitsConfig

if TYPE_CHECKING:
    from mitup_bot.models import User


class LimitsState:
    """Holds the runtime-resolved limits. Kept on a class attribute rather than a module global so
    `configure` can replace it wholesale; defaults to the shipped `LimitsConfig` values."""

    config: LimitsConfig = LimitsConfig()


def configure(config: LimitsConfig):
    """Adopt the merged limits configuration. Called once at startup; idempotent on replace."""
    LimitsState.config = config


def active_meetings_cap(user: User) -> int:
    """Maximum active meetings the user may own, raised for premium supporters."""
    config = LimitsState.config
    return config.premium_active_meetings if user.is_premium else config.free_active_meetings


def scheduling_horizon_days(user: User) -> int:
    """How many days ahead the user may set a meeting's start date, extended for premium supporters."""
    config = LimitsState.config
    return config.premium_scheduling_horizon_days if user.is_premium else config.free_scheduling_horizon_days


def at_active_meetings_cap(user: User) -> bool:
    """Whether the user already owns their cap's worth of active meetings.

    Counts only active meetings, so grandfathered over-cap users can still edit and leave their
    existing meetings; they are simply blocked from adding more until they drop below the cap.
    """
    active = sum(1 for meeting in user.meetups if meeting.active)
    return active >= active_meetings_cap(user)


def within_scheduling_horizon(user: User, when: dt.datetime) -> bool:
    """Whether `when` falls on or before the user's furthest schedulable date.

    The picked date is compared in the user's timezone, and the horizon boundary itself is allowed.
    A naive datetime is read as UTC, matching how meeting datetimes are persisted.
    """
    aware = when if when.tzinfo else when.replace(tzinfo=dt.UTC)
    picked_date = user.datetime_in_tz(aware).date()
    latest = user.now_in_tz().date() + dt.timedelta(days=scheduling_horizon_days(user))
    return picked_date <= latest
