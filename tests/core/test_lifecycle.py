"""Tests for the meeting lifecycle policy.

The durations themselves are pinned here — they are product decisions the jobs, handlers and models
all read, so a change to any of them should show up as a failure in one place. The behaviour of the
SQL conditions generated from these values lives in
`tests/data/db_behavior/test_lifecycle_windows.py`, which runs them against real Postgres.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from mitup_bot.config import LimitsConfig
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.supporter import LEVEL_ORDER, SupporterLevel

FREE_LEVELS = (SupporterLevel.NONE, SupporterLevel.HOST_1)
PAYING_LEVELS = (SupporterLevel.HOST_2, SupporterLevel.HOST_3)
# Named indirectly so the mutation attempt below stays a `setattr` call: written as an assignment it
# is a static type error, and the point of the test is the runtime behaviour.
MUTATED_FIELD = "inactive_retention"


def test_tier_independent_durations():
    assert LifecyclePolicy.get().max_timeout_minutes == 24 * 60
    assert LifecyclePolicy.get().max_duration == dt.timedelta(days=7)
    assert LifecyclePolicy.get().left_owner_dateless_lifetime == dt.timedelta(days=30)
    assert LifecyclePolicy.get().deletion_warning_lead == dt.timedelta(days=7)


def test_get_without_a_level_resolves_the_free_policy():
    """A caller with no supporter context in hand omits the level; the tier-independent durations it
    reads are the same whichever policy answers."""
    assert LifecyclePolicy.get() is LifecyclePolicy.get(SupporterLevel.NONE)


@pytest.mark.parametrize("level", LEVEL_ORDER)
def test_tier_independent_durations_are_identical_on_every_policy(level: SupporterLevel):
    """These four are read without a level in hand, so a tier that quietly diverged on one would give
    the no-context callers a different answer from the owner-aware ones."""
    policy = LifecyclePolicy.get(level)
    default = LifecyclePolicy.get()
    assert policy.max_timeout_minutes == default.max_timeout_minutes
    assert policy.max_duration == default.max_duration
    assert policy.left_owner_dateless_lifetime == default.left_owner_dateless_lifetime
    assert policy.deletion_warning_lead == default.deletion_warning_lead


def test_policy_is_frozen():
    """The policy is a value, not mutable state: nothing may retune a duration at runtime."""
    with pytest.raises(FrozenInstanceError):
        setattr(LifecyclePolicy.get(SupporterLevel.NONE), MUTATED_FIELD, dt.timedelta(days=1))


@pytest.mark.parametrize("level", FREE_LEVELS)
def test_free_levels_share_the_short_windows(level: SupporterLevel):
    policy = LifecyclePolicy.get(level)
    assert policy.dateless_lifetime == dt.timedelta(days=90)
    assert policy.inactive_retention == dt.timedelta(days=90)


@pytest.mark.parametrize("level", FREE_LEVELS)
def test_free_dateless_lifetime_matches_the_free_scheduling_horizon(level: SupporterLevel):
    """A free owner cannot pick a date beyond the horizon, so an undated meeting does not outlive it
    either — the two numbers are one product decision and have to move together."""
    assert LifecyclePolicy.get(level).dateless_lifetime == dt.timedelta(
        days=LimitsConfig().free_scheduling_horizon_days
    )


@pytest.mark.parametrize("level", PAYING_LEVELS)
def test_paying_levels_share_the_long_windows(level: SupporterLevel):
    """The Organizer horizon is unlimited, but a dateless meeting is a draft and drafts end, so
    HOST_3 shares the Patron windows rather than living forever."""
    policy = LifecyclePolicy.get(level)
    assert policy.dateless_lifetime == dt.timedelta(days=365)
    assert policy.inactive_retention == dt.timedelta(days=365)


@pytest.mark.parametrize("level", LEVEL_ORDER)
def test_every_level_resolves_one_shared_policy_instance(level: SupporterLevel):
    """The per-tier policies are constants, so resolving a level never builds a new object."""
    assert LifecyclePolicy.get(level) is LifecyclePolicy.get(level)


@pytest.mark.parametrize("level", LEVEL_ORDER)
def test_warning_lands_one_lead_before_the_deletion(level: SupporterLevel):
    """Whatever the tier's retention, the warning is exactly one lead time before the deletion."""
    policy = LifecyclePolicy.get(level)
    assert policy.inactive_retention - policy.deletion_warning_delay == policy.deletion_warning_lead
    assert policy.deletion_warning_delay > dt.timedelta(0)


def test_warning_delays_match_the_intervals_the_jobs_render():
    assert {level: LifecyclePolicy.get(level).deletion_warning_delay for level in LEVEL_ORDER} == {
        SupporterLevel.NONE: dt.timedelta(days=83),
        SupporterLevel.HOST_1: dt.timedelta(days=83),
        SupporterLevel.HOST_2: dt.timedelta(days=358),
        SupporterLevel.HOST_3: dt.timedelta(days=358),
    }


@pytest.mark.parametrize(
    "duration_of",
    [
        lambda policy: policy.dateless_lifetime,
        lambda policy: policy.inactive_retention,
        lambda policy: policy.deletion_warning_delay,
    ],
    ids=["dateless_lifetime", "inactive_retention", "deletion_warning_delay"],
)
def test_every_level_is_grouped(duration_of: Callable[[LifecyclePolicy], dt.timedelta]):
    """A per-owner SQL condition is generated from these groupings, so a level missing from one would
    silently exclude that tier's meetings from the sweep."""
    grouped = LifecyclePolicy.levels_by_duration(duration_of)
    assert sorted(level for levels in grouped.values() for level in levels) == sorted(LEVEL_ORDER)


def test_levels_by_duration_groups_the_levels_that_share_a_value():
    assert LifecyclePolicy.levels_by_duration(lambda policy: policy.dateless_lifetime) == {
        dt.timedelta(days=90): list(FREE_LEVELS),
        dt.timedelta(days=365): list(PAYING_LEVELS),
    }


def test_levels_by_duration_keeps_one_group_when_every_level_agrees():
    """`max_duration` is tier-independent, so reading it off each policy collapses to one branch."""
    assert LifecyclePolicy.levels_by_duration(lambda policy: policy.max_duration) == {
        LifecyclePolicy.get().max_duration: list(LEVEL_ORDER)
    }


@pytest.mark.parametrize(
    ("duration", "days"),
    [(dt.timedelta(days=1), 1), (dt.timedelta(days=365), 365), (dt.timedelta(0), 0)],
)
def test_interval_days_converts_whole_days(duration: dt.timedelta, days: int):
    assert LifecyclePolicy.interval_days(duration) == days


def test_interval_days_rejects_a_sub_day_duration():
    """Rendering would silently truncate the remainder, so a policy value that is not whole days is
    rejected instead of quietly shortening the window."""
    with pytest.raises(ValueError, match="whole number of days"):
        LifecyclePolicy.interval_days(dt.timedelta(days=1, hours=6))


@pytest.mark.parametrize("level", LEVEL_ORDER)
def test_every_policy_duration_renders_as_whole_days(level: SupporterLevel):
    policy = LifecyclePolicy.get(level)
    durations = (
        policy.max_duration,
        policy.left_owner_dateless_lifetime,
        policy.deletion_warning_lead,
        policy.dateless_lifetime,
        policy.inactive_retention,
        policy.deletion_warning_delay,
    )
    assert all(LifecyclePolicy.interval_days(duration) > 0 for duration in durations)
