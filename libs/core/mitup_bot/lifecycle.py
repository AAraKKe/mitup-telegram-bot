"""Single source of truth for every duration in a meeting's lifecycle.

A meeting walks one path: it is activated (created, or reactivated from the past-meetings list),
it stays active for a bounded time, the deactivation sweep turns it inactive and stamps
`expiration_time`, the owner gets one warning, and the permanent deletion removes it. Every hop's
duration lives on `LifecyclePolicy` — the jobs, the handlers and the models read them from it instead
of carrying literals of their own.

Two durations depend on the owner's `SupporterLevel`; the rest carry the same value on every tier.
All of them are read the same way — `LifecyclePolicy.get(level).<duration>` — and a caller with no
supporter context in hand omits the level, which resolves to the free policy. The tier is read at
query time from the owner's *current* level, so a tier change re-times the meetings that owner
already has: a free host who becomes a Patron immediately gets the longer windows on their existing
inactive meetings, and a lapsed Patron the shorter ones.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from mitup_bot.supporter import LEVEL_ORDER, SupporterLevel


@dataclass(frozen=True)
class LifecyclePolicy:
    """The durations that shape a meeting's life, from activation to permanent deletion.

    One frozen instance per distinct set of tier values, reached with `get`.
    """

    # How long a dateless meeting stays active after activation: nothing else ever ends an undated
    # draft. Mirrors the tier's scheduling horizon — the furthest it could have dated the meeting.
    dateless_lifetime: dt.timedelta

    # How long an inactive meeting is retained before permanent deletion — the window an owner can
    # still reactivate it in. Storing it is the cost supporters offset, so they are kept longer.
    inactive_retention: dt.timedelta

    # Ceiling for the owner's `Settings.timeout`, the delay between a meeting's end and its
    # deactivation. Without it a timeout keeps meetings active forever, so they are never cleaned up.
    max_timeout_minutes: int = 24 * 60

    # Longest span a single meeting may cover, from its start to its end.
    max_duration: dt.timedelta = dt.timedelta(days=7)

    # A dateless meeting whose owner has LEFT deactivates this soon instead of running its tier's
    # window, so the owner stops owning an active meeting and becomes purgeable by user_cleanup.
    left_owner_dateless_lifetime: dt.timedelta = dt.timedelta(days=30)

    # How long before the retention ends the owner is warned. The warning copy names this number of
    # days, so the two must move together.
    deletion_warning_lead: dt.timedelta = dt.timedelta(days=7)

    @property
    def deletion_warning_delay(self) -> dt.timedelta:
        """How long after deactivation this tier's owner is warned about the coming deletion, always
        one `deletion_warning_lead` before it whatever the tier's retention."""
        return self.inactive_retention - self.deletion_warning_lead

    @classmethod
    def get(cls, level: SupporterLevel | None = None) -> LifecyclePolicy:
        """The policy a meeting owned by this supporter level runs on.

        A caller holding no supporter context omits the level and gets the free policy, which carries
        the same values as every other tier for the durations no tier changes.
        """
        match level:
            case None | SupporterLevel.NONE | SupporterLevel.HOST_1:
                return FREE_POLICY
            case SupporterLevel.HOST_2 | SupporterLevel.HOST_3:
                return PATRON_POLICY
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def levels_by_duration(
        cls, duration_of: Callable[[LifecyclePolicy], dt.timedelta]
    ) -> dict[dt.timedelta, list[SupporterLevel]]:
        """Group every level by the duration `duration_of` reads off that level's policy.

        A per-owner SQL condition needs one branch per *distinct* duration, not one per level, so a
        tier sharing an existing duration adds no SQL.
        """
        grouped: dict[dt.timedelta, list[SupporterLevel]] = {}
        for level in LEVEL_ORDER:
            grouped.setdefault(duration_of(cls.get(level)), []).append(level)
        return grouped

    @staticmethod
    def interval_days(duration: dt.timedelta) -> int:
        """The duration as whole days, for rendering a Postgres `INTERVAL` literal.

        A sub-day remainder is a policy mistake, rejected rather than silently truncated.
        """
        if duration % dt.timedelta(days=1):
            raise ValueError(f"Lifecycle duration {duration!r} is not a whole number of days.")
        return duration.days


FREE_POLICY = LifecyclePolicy(dateless_lifetime=dt.timedelta(days=90), inactive_retention=dt.timedelta(days=90))

# The Organizer scheduling horizon is unlimited, but a draft still ends and storage is still paid
# for, so that tier shares the Patron windows rather than getting one of its own.
PATRON_POLICY = LifecyclePolicy(dateless_lifetime=dt.timedelta(days=365), inactive_retention=dt.timedelta(days=365))
