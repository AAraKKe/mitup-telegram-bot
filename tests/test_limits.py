import datetime as dt

import pytest
from freezegun import freeze_time

from mitup_bot import limits
from mitup_bot.config import LimitsConfig
from mitup_bot.models import User
from tests.helpers import create_meetup

# Europe/Madrid is UTC+1 in winter; the user_with_settings owner uses that zone, so a frozen
# winter instant keeps the horizon maths free of DST ambiguity.
FROZEN_NOW = "2025-01-15 12:00:00"  # UTC


@pytest.fixture
def configured_limits(monkeypatch: pytest.MonkeyPatch) -> LimitsConfig:
    """Small, explicit caps so the boundaries are easy to reason about in assertions."""
    config = LimitsConfig(
        free_active_meetings=2,
        premium_active_meetings=4,
        free_scheduling_horizon_days=31,
        premium_scheduling_horizon_days=365,
    )
    monkeypatch.setattr(limits.LimitsState, "config", config)
    return config


def test_configure_replaces_the_active_config():
    original = limits.LimitsState.config
    try:
        limits.configure(LimitsConfig(free_active_meetings=1))
        assert limits.LimitsState.config.free_active_meetings == 1
    finally:
        limits.configure(original)


def test_active_meetings_cap_is_raised_for_premium(user_with_settings: User, configured_limits: LimitsConfig):
    assert limits.active_meetings_cap(user_with_settings) == configured_limits.free_active_meetings
    user_with_settings.is_premium = True
    assert limits.active_meetings_cap(user_with_settings) == configured_limits.premium_active_meetings


def test_scheduling_horizon_days_is_raised_for_premium(user_with_settings: User, configured_limits: LimitsConfig):
    assert limits.scheduling_horizon_days(user_with_settings) == configured_limits.free_scheduling_horizon_days
    user_with_settings.is_premium = True
    assert limits.scheduling_horizon_days(user_with_settings) == configured_limits.premium_scheduling_horizon_days


def test_at_active_meetings_cap_counts_only_active(user_with_settings: User, configured_limits: LimitsConfig):
    # The fixture owner has two active meetings; the free cap is two, so they are at the cap.
    assert limits.at_active_meetings_cap(user_with_settings) is True

    # Grandfathering: wrapping one up (making it inactive) drops the count below the cap, so the
    # user can create/reactivate again without any retroactive deactivation of the rest.
    user_with_settings.meetups[0].active = False
    assert limits.at_active_meetings_cap(user_with_settings) is False


def test_over_cap_user_is_still_capped_but_not_deactivated(user_with_settings: User, configured_limits: LimitsConfig):
    # A grandfathered user sitting above the cap keeps every meeting; they are simply blocked.
    # create_meetup(owner=...) appends to owner.meetups on its own, pushing the count past the cap.
    create_meetup(id=99, title="Extra", owner=user_with_settings)
    active_before = [m for m in user_with_settings.meetups if m.active]
    assert len(active_before) > configured_limits.free_active_meetings
    assert limits.at_active_meetings_cap(user_with_settings) is True
    assert all(m.active for m in user_with_settings.meetups)


@freeze_time(FROZEN_NOW, tz_offset=0)
def test_within_scheduling_horizon_boundary_is_allowed(user_with_settings: User, configured_limits: LimitsConfig):
    today = user_with_settings.now_in_tz().date()
    horizon = configured_limits.free_scheduling_horizon_days

    exactly_on_horizon = dt.datetime.combine(today + dt.timedelta(days=horizon), dt.time(12, 0), tzinfo=dt.UTC)
    one_day_beyond = dt.datetime.combine(today + dt.timedelta(days=horizon + 1), dt.time(12, 0), tzinfo=dt.UTC)

    assert limits.within_scheduling_horizon(user_with_settings, exactly_on_horizon) is True
    assert limits.within_scheduling_horizon(user_with_settings, one_day_beyond) is False


@freeze_time(FROZEN_NOW, tz_offset=0)
def test_within_scheduling_horizon_raised_for_premium(user_with_settings: User, configured_limits: LimitsConfig):
    today = user_with_settings.now_in_tz().date()
    beyond_free = dt.datetime.combine(today + dt.timedelta(days=90), dt.time(12, 0), tzinfo=dt.UTC)

    assert limits.within_scheduling_horizon(user_with_settings, beyond_free) is False
    user_with_settings.is_premium = True
    assert limits.within_scheduling_horizon(user_with_settings, beyond_free) is True


@freeze_time(FROZEN_NOW, tz_offset=0)
def test_within_scheduling_horizon_reads_naive_datetime_as_utc(
    user_with_settings: User, configured_limits: LimitsConfig
):
    today = user_with_settings.now_in_tz().date()
    naive_on_horizon = dt.datetime.combine(
        today + dt.timedelta(days=configured_limits.free_scheduling_horizon_days), dt.time(0, 0)
    )
    assert naive_on_horizon.tzinfo is None
    # Must not raise and must treat the value as UTC rather than local system time.
    assert limits.within_scheduling_horizon(user_with_settings, naive_on_horizon) is True
