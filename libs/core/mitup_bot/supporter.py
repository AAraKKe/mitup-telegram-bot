"""Single source of truth for supporter-tier policy.

Every support-gated decision — the guard, the limit caps, and the display badge — receives a
`SupporterLevel` and asks this module for the answer, instead of comparing levels inline at the call
site. Adding a tier, moving a threshold, or gating a new capability becomes a change confined to this
module, and no call site ever hard-codes a level comparison.

The capability caps resolve from `LimitsConfig`, adopted once at startup via `configure` (mirroring
the holder pattern in `db`/`timezone_api`); the default holder is the shipped `LimitsConfig`, so any
entry point that never calls `configure` still resolves the shipped limits. The Patreon amount
thresholds live on `PatreonConfig` and are passed in explicitly by the callers that already hold it
(the OAuth callback and the membership job), so this module keeps a single configured holder.
"""

from enum import StrEnum
from typing import Literal, assert_never, overload

from mitup_bot.config import LimitsConfig, PatreonConfig
from mitup_bot.emojis import Emojis


class SupporterLevel(StrEnum):
    """Support tier a `User` sits at. Stored as VARCHAR (see `User.supporter_level`), mirroring the
    `UserStatus` pattern; the tier ordering is owned by this module (`LEVEL_ORDER`/`meets`), not by
    the enum members themselves."""

    NONE = "none"
    HOST_1 = "host_1"
    HOST_2 = "host_2"
    HOST_3 = "host_3"


# Ascending rank: the index in this tuple is the comparison key, so `meets` never compares enum
# members directly and adding a tier is a single-line change here.
LEVEL_ORDER: tuple[SupporterLevel, ...] = (
    SupporterLevel.NONE,
    SupporterLevel.HOST_1,
    SupporterLevel.HOST_2,
    SupporterLevel.HOST_3,
)


class PolicyState:
    """Holds the runtime-resolved limits. Kept on a class attribute rather than a module global so
    `configure` can replace it wholesale; defaults to the shipped `LimitsConfig` values."""

    config: LimitsConfig = LimitsConfig()


def configure(config: LimitsConfig):
    """Adopt the merged limits configuration. Called once at startup; idempotent on replace."""
    PolicyState.config = config


def rank(level: SupporterLevel) -> int:
    return LEVEL_ORDER.index(level)


def meets(level: SupporterLevel, minimum: SupporterLevel) -> bool:
    """Whether `level` reaches at least `minimum` in the tier ordering. The ordering primitive every
    guard and gated call site resolves through instead of comparing levels itself."""
    return rank(level) >= rank(minimum)


def is_supporter(level: SupporterLevel) -> bool:
    """Whether the level is a paying tier at all (any level above NONE)."""
    return meets(level, SupporterLevel.HOST_1)


def highest(*levels: SupporterLevel) -> SupporterLevel:
    """The highest-ranked of ``levels``. The resolution rule between an earned tier and the
    manually-granted floor (``User.granted_supporter_level``): every writer of
    ``User.supporter_level`` resolves through this, so a grant survives any Patreon transition."""
    return max(levels, key=rank)


def level_for_amount(cents: int, config: PatreonConfig) -> SupporterLevel:
    """The tier an active member's `currently_entitled_amount_cents` maps to: the highest threshold
    it reaches. An active member below the lowest threshold still counts as HOST_1 — an active
    patron never gets zero recognition; only the limit tiers need the thresholds. Callers must only
    pass amounts for members already known to be active; a non-member maps to NONE at the call site.
    """
    if cents >= config.organizer_min_cents:
        return SupporterLevel.HOST_3
    if cents >= config.patron_min_cents:
        return SupporterLevel.HOST_2
    return SupporterLevel.HOST_1


@overload
def active_meetings_cap(level: Literal[SupporterLevel.HOST_3]) -> None: ...
@overload
def active_meetings_cap(
    level: Literal[SupporterLevel.NONE, SupporterLevel.HOST_1, SupporterLevel.HOST_2],
) -> int: ...
@overload
def active_meetings_cap(level: SupporterLevel) -> int | None: ...
def active_meetings_cap(level: SupporterLevel) -> int | None:
    """Maximum active meetings a level may own, or None (unlimited) for HOST_3."""
    config = PolicyState.config
    match level:
        case SupporterLevel.NONE | SupporterLevel.HOST_1:
            return config.free_active_meetings
        case SupporterLevel.HOST_2:
            return config.patron_active_meetings
        case SupporterLevel.HOST_3:
            return None
        case _ as unreachable:
            assert_never(unreachable)


@overload
def scheduling_horizon_days(level: Literal[SupporterLevel.HOST_3]) -> None: ...
@overload
def scheduling_horizon_days(
    level: Literal[SupporterLevel.NONE, SupporterLevel.HOST_1, SupporterLevel.HOST_2],
) -> int: ...
@overload
def scheduling_horizon_days(level: SupporterLevel) -> int | None: ...
def scheduling_horizon_days(level: SupporterLevel) -> int | None:
    """How many days ahead a level may schedule a meeting, or None (unlimited) for HOST_3."""
    config = PolicyState.config
    match level:
        case SupporterLevel.NONE | SupporterLevel.HOST_1:
            return config.free_scheduling_horizon_days
        case SupporterLevel.HOST_2:
            return config.patron_scheduling_horizon_days
        case SupporterLevel.HOST_3:
            return None
        case _ as unreachable:
            assert_never(unreachable)


@overload
def participant_capacity(level: Literal[SupporterLevel.HOST_2, SupporterLevel.HOST_3]) -> None: ...
@overload
def participant_capacity(level: Literal[SupporterLevel.NONE, SupporterLevel.HOST_1]) -> int: ...
@overload
def participant_capacity(level: SupporterLevel) -> int | None: ...
def participant_capacity(level: SupporterLevel) -> int | None:
    """The per-meeting participant cap for a level, or None (uncapped) for HOST_2 and HOST_3."""
    config = PolicyState.config
    match level:
        case SupporterLevel.NONE | SupporterLevel.HOST_1:
            return config.free_participant_capacity
        case SupporterLevel.HOST_2 | SupporterLevel.HOST_3:
            return None
        case _ as unreachable:
            assert_never(unreachable)


def badge(level: SupporterLevel) -> str | None:
    """The emoji that decorates a level's `display_name`, or None for NONE (no badge)."""
    match level:
        case SupporterLevel.NONE:
            return None
        case SupporterLevel.HOST_1:
            return Emojis.HOST_1.value
        case SupporterLevel.HOST_2:
            return Emojis.HOST_2.value
        case SupporterLevel.HOST_3:
            return Emojis.HOST_3.value
        case _ as unreachable:
            assert_never(unreachable)
