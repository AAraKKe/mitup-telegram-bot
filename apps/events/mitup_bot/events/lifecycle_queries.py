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
