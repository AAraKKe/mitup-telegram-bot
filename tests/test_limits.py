import datetime as dt

import pytest
from freezegun import freeze_time

from mitup_bot import limits, supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.models import User
from mitup_bot.supporter import SupporterLevel
from tests.helpers import create_meetup

# Europe/Madrid is UTC+1 in winter; the user_with_settings owner uses that zone, so a frozen
# winter instant keeps the horizon maths free of DST ambiguity.
FROZEN_NOW = "2025-01-15 12:00:00"  # UTC


@pytest.fixture
def configured_limits(monkeypatch: pytest.MonkeyPatch) -> LimitsConfig:
    """Small, explicit caps so the boundaries are easy to reason about in assertions."""
    config = LimitsConfig(
        free_active_meetings=2,
        patron_active_meetings=4,
        free_scheduling_horizon_days=31,
        patron_scheduling_horizon_days=365,
        free_participant_capacity=5,
    )
    monkeypatch.setattr(supporter.PolicyState, "config", config)
    return config


@pytest.mark.parametrize(
    "level,expected_attr",
    [
        (SupporterLevel.NONE, "free_active_meetings"),
        (SupporterLevel.SUPPORTER, "free_active_meetings"),
        (SupporterLevel.PATRON, "patron_active_meetings"),
    ],
    ids=["none", "supporter", "patron"],
)
def test_active_meetings_cap_by_level(
    user_with_settings: User, configured_limits: LimitsConfig, level: SupporterLevel, expected_attr: str
):
    user_with_settings.supporter_level = level
    assert limits.active_meetings_cap(user_with_settings) == getattr(configured_limits, expected_attr)


def test_active_meetings_cap_is_unlimited_for_organizer(user_with_settings: User, configured_limits: LimitsConfig):
    user_with_settings.supporter_level = SupporterLevel.ORGANIZER
    assert limits.active_meetings_cap(user_with_settings) is None


@pytest.mark.parametrize(
    "level,expected_attr",
    [
        (SupporterLevel.NONE, "free_scheduling_horizon_days"),
        (SupporterLevel.SUPPORTER, "free_scheduling_horizon_days"),
        (SupporterLevel.PATRON, "patron_scheduling_horizon_days"),
    ],
    ids=["none", "supporter", "patron"],
)
def test_scheduling_horizon_days_by_level(
    user_with_settings: User, configured_limits: LimitsConfig, level: SupporterLevel, expected_attr: str
):
    user_with_settings.supporter_level = level
    assert limits.scheduling_horizon_days(user_with_settings) == getattr(configured_limits, expected_attr)


def test_scheduling_horizon_is_unlimited_for_organizer(user_with_settings: User, configured_limits: LimitsConfig):
    user_with_settings.supporter_level = SupporterLevel.ORGANIZER
    assert limits.scheduling_horizon_days(user_with_settings) is None


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.NONE, 5),
        (SupporterLevel.SUPPORTER, 5),
        (SupporterLevel.PATRON, None),
        (SupporterLevel.ORGANIZER, None),
    ],
    ids=["none", "supporter", "patron", "organizer"],
)
def test_participant_capacity_by_level(
    user_with_settings: User, configured_limits: LimitsConfig, level: SupporterLevel, expected: int | None
):
    # configured_limits pins free_participant_capacity=5; Patron and Organizer are uncapped.
    user_with_settings.supporter_level = level
    assert limits.participant_capacity(user_with_settings) == expected


@pytest.mark.parametrize(
    "level,max_members,expected",
    [
        (SupporterLevel.NONE, None, 5),  # capped + no explicit limit resolves to the cap
        (SupporterLevel.NONE, 3, 3),  # capped + explicit below cap is left untouched
        (SupporterLevel.NONE, 10, 5),  # capped + explicit above cap is clamped down (grandfathered)
        (SupporterLevel.PATRON, None, None),  # uncapped + no explicit limit stays unlimited
        (SupporterLevel.PATRON, 100, 100),  # uncapped + explicit limit is honored as-is
    ],
    ids=["free_no_limit", "free_below_cap", "free_above_cap", "patron_no_limit", "patron_explicit"],
)
def test_effective_participant_capacity(
    user_with_settings: User,
    configured_limits: LimitsConfig,  # pins free_participant_capacity=5
    level: SupporterLevel,
    max_members: int | None,
    expected: int | None,
):
    user_with_settings.supporter_level = level
    assert limits.effective_participant_capacity(user_with_settings, max_members) == expected


def test_at_active_meetings_cap_counts_only_active(user_with_settings: User, configured_limits: LimitsConfig):
    # The fixture owner has two active meetings; the free cap is two, so they are at the cap.
    assert limits.at_active_meetings_cap(user_with_settings) is True

    # Grandfathering: wrapping one up (making it inactive) drops the count below the cap, so the
    # user can create/reactivate again without any retroactive deactivation of the rest.
    user_with_settings.meetups[0].active = False
    assert limits.at_active_meetings_cap(user_with_settings) is False


def test_organizer_is_never_at_active_meetings_cap(user_with_settings: User, configured_limits: LimitsConfig):
    # The fixture owner is at the free cap, but an Organizer tier is uncapped.
    user_with_settings.supporter_level = SupporterLevel.ORGANIZER
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
def test_within_scheduling_horizon_raised_for_patron(user_with_settings: User, configured_limits: LimitsConfig):
    today = user_with_settings.now_in_tz().date()
    beyond_free = dt.datetime.combine(today + dt.timedelta(days=90), dt.time(12, 0), tzinfo=dt.UTC)

    assert limits.within_scheduling_horizon(user_with_settings, beyond_free) is False
    user_with_settings.supporter_level = SupporterLevel.PATRON
    assert limits.within_scheduling_horizon(user_with_settings, beyond_free) is True


@freeze_time(FROZEN_NOW, tz_offset=0)
def test_organizer_has_no_scheduling_horizon(user_with_settings: User, configured_limits: LimitsConfig):
    today = user_with_settings.now_in_tz().date()
    far_future = dt.datetime.combine(today + dt.timedelta(days=5000), dt.time(12, 0), tzinfo=dt.UTC)

    user_with_settings.supporter_level = SupporterLevel.ORGANIZER
    assert limits.within_scheduling_horizon(user_with_settings, far_future) is True


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
