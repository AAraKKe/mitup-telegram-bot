"""SQL fragments that turn the `LifecyclePolicy` durations into per-owner conditions.

The policy durations are plain `timedelta`s; the sweep and cleanup jobs need them as Postgres
`INTERVAL` comparisons that pick the right value for each meeting's owner, and both build them here
so the interval rendering exists once.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlalchemy.orm import Mapped
from sqlmodel import and_, col, func, literal, or_

from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import User
from mitup_bot.supporter import SupporterLevel


def sql_interval(duration: dt.timedelta) -> ColumnElement[dt.timedelta]:
    """`duration` rendered as a Postgres interval, e.g. `CAST('90 days' AS INTERVAL)`."""
    return func.cast(literal(f"{LifecyclePolicy.interval_days(duration)} days"), INTERVAL)


def owner_tier_window_elapsed(
    timestamp: Mapped[Any], duration_of: Callable[[LifecyclePolicy], dt.timedelta]
) -> ColumnElement[bool]:
    """Whether the datetime column `timestamp` plus the owner's tier duration is already past.

    `duration_of` picks which window to compare, read off each level's policy. The enclosing
    statement must join `users` as the meeting's owner for `supporter_level` to be in scope.
    """
    conditions = [
        and_(col(User.supporter_level).in_(levels), timestamp + sql_interval(duration) < func.now())
        for duration, levels in LifecyclePolicy.levels_by_duration(duration_of).items()
    ]
    # `or_` types its first clause separately, so the branches are spread rather than passed as a
    # single list. `levels_by_duration` covers every level, so there is always at least one.
    first, *rest = conditions
    return or_(first, *rest)


def resolved_windows(duration_of: Callable[[LifecyclePolicy], dt.timedelta]) -> dict[SupporterLevel, int]:
    """The window each supporter level currently runs on, in whole days.

    Generated from the same `levels_by_duration` grouping the predicate's branches are, so a tier
    that moves to a different duration moves its label with it instead of needing a mirror kept in
    step by hand. It is what lets a decision name the window that produced it: the statements bake
    their intervals in at import time, so a policy edit otherwise ships with the image tag as its
    only evidence and the running container cannot be asked what it is enforcing.
    """
    return {
        level: LifecyclePolicy.interval_days(duration)
        for duration, levels in LifecyclePolicy.levels_by_duration(duration_of).items()
        for level in levels
    }


def loggable_windows(duration_of: Callable[[LifecyclePolicy], dt.timedelta]) -> dict[str, int]:
    """`resolved_windows` keyed by the level's stored value, for a log field."""
    return {level.value: days for level, days in resolved_windows(duration_of).items()}
