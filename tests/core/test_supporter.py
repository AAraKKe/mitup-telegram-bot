import pytest

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig, PatreonConfig
from mitup_bot.emojis import Emojis
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages
from tests.helpers import create_patreon_config


@pytest.fixture
def thresholds() -> PatreonConfig:
    """A PatreonConfig with the shipped 300/500/1000 tier thresholds level_for_amount reads."""
    return create_patreon_config()


def test_configure_replaces_the_active_config():
    original = supporter.PolicyState.config
    try:
        supporter.configure(LimitsConfig(free_active_meetings=1))
        assert supporter.PolicyState.config.free_active_meetings == 1
    finally:
        supporter.configure(original)


@pytest.mark.parametrize(
    "level,minimum,expected",
    [
        (SupporterLevel.NONE, SupporterLevel.NONE, True),
        (SupporterLevel.HOST_1, SupporterLevel.HOST_2, False),
        (SupporterLevel.HOST_2, SupporterLevel.HOST_2, True),
        (SupporterLevel.HOST_3, SupporterLevel.HOST_2, True),
        (SupporterLevel.NONE, SupporterLevel.HOST_1, False),
    ],
)
def test_meets_respects_tier_ordering(level: SupporterLevel, minimum: SupporterLevel, expected: bool):
    assert supporter.meets(level, minimum) is expected


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.NONE, False),
        (SupporterLevel.HOST_1, True),
        (SupporterLevel.HOST_2, True),
        (SupporterLevel.HOST_3, True),
    ],
)
def test_is_supporter(level: SupporterLevel, expected: bool):
    assert supporter.is_supporter(level) is expected


@pytest.mark.parametrize(
    "earned,granted,expected",
    [
        (SupporterLevel.NONE, SupporterLevel.NONE, SupporterLevel.NONE),
        (SupporterLevel.HOST_1, SupporterLevel.NONE, SupporterLevel.HOST_1),
        (SupporterLevel.NONE, SupporterLevel.HOST_2, SupporterLevel.HOST_2),
        (SupporterLevel.HOST_3, SupporterLevel.HOST_1, SupporterLevel.HOST_3),
        (SupporterLevel.HOST_2, SupporterLevel.HOST_2, SupporterLevel.HOST_2),
    ],
)
def test_highest_returns_the_higher_ranked_level(
    earned: SupporterLevel, granted: SupporterLevel, expected: SupporterLevel
):
    assert supporter.highest(earned, granted) is expected


@pytest.mark.parametrize(
    "cents,expected",
    [
        (0, SupporterLevel.HOST_1),  # active member below the lowest threshold still counts
        (299, SupporterLevel.HOST_1),  # below supporter_min but active -> SUPPORTER floor
        (300, SupporterLevel.HOST_1),  # at the nominal supporter price
        (499, SupporterLevel.HOST_1),  # just under the patron threshold
        (500, SupporterLevel.HOST_2),  # exactly at the patron threshold
        (700, SupporterLevel.HOST_2),  # a custom 7€ pledge lands on Patron
        (999, SupporterLevel.HOST_2),  # just under the organizer threshold
        (1000, SupporterLevel.HOST_3),  # exactly at the organizer threshold
        (5000, SupporterLevel.HOST_3),  # well above the top threshold
    ],
)
def test_level_for_amount_boundaries(thresholds: PatreonConfig, cents: int, expected: SupporterLevel):
    assert supporter.level_for_amount(cents, thresholds) is expected


@pytest.fixture
def configured_policy(monkeypatch: pytest.MonkeyPatch) -> LimitsConfig:
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
    "level,expected",
    [
        (SupporterLevel.NONE, 2),
        (SupporterLevel.HOST_1, 2),
        (SupporterLevel.HOST_2, 4),
        (SupporterLevel.HOST_3, None),
    ],
)
def test_active_meetings_cap_by_level(configured_policy: LimitsConfig, level: SupporterLevel, expected: int | None):
    assert supporter.active_meetings_cap(level) == expected


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.NONE, 31),
        (SupporterLevel.HOST_1, 31),
        (SupporterLevel.HOST_2, 365),
        (SupporterLevel.HOST_3, None),
    ],
)
def test_scheduling_horizon_days_by_level(configured_policy: LimitsConfig, level: SupporterLevel, expected: int | None):
    assert supporter.scheduling_horizon_days(level) == expected


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.NONE, 5),
        (SupporterLevel.HOST_1, 5),
        (SupporterLevel.HOST_2, None),
        (SupporterLevel.HOST_3, None),
    ],
)
def test_participant_capacity_by_level(configured_policy: LimitsConfig, level: SupporterLevel, expected: int | None):
    assert supporter.participant_capacity(level) == expected


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.NONE, None),
        (SupporterLevel.HOST_1, Emojis.HOST_1.value),
        (SupporterLevel.HOST_2, Emojis.HOST_2.value),
        (SupporterLevel.HOST_3, Emojis.HOST_3.value),
    ],
)
def test_badge_per_level(level: SupporterLevel, expected: str | None):
    assert supporter.badge(level) == expected


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.HOST_1, SupporterNotificationMessages.SUPPORTER_UNLOCKED),
        (SupporterLevel.HOST_2, SupporterNotificationMessages.PATRON_UNLOCKED),
        (SupporterLevel.HOST_3, SupporterNotificationMessages.ORGANIZER_UNLOCKED),
    ],
)
def test_unlocked_for_maps_each_paying_tier(level: SupporterLevel, expected: SupporterNotificationMessages):
    assert SupporterNotificationMessages.unlocked_for(level) is expected


def test_unlocked_for_rejects_none_tier():
    # NONE never reaches the resolver: callers only announce an unlock once a paying tier is confirmed.
    with pytest.raises(ValueError, match="NONE tier"):
        SupporterNotificationMessages.unlocked_for(SupporterLevel.NONE)


@pytest.mark.parametrize(
    "level,expected",
    [
        (SupporterLevel.HOST_1, SupporterNotificationMessages.SUPPORTER_TIER_SET),
        (SupporterLevel.HOST_2, SupporterNotificationMessages.PATRON_TIER_SET),
    ],
)
def test_downgraded_to_maps_lower_paying_tiers(level: SupporterLevel, expected: SupporterNotificationMessages):
    assert SupporterNotificationMessages.downgraded_to(level) is expected


@pytest.mark.parametrize(
    "level,match",
    [
        # ORGANIZER is the top tier, so nothing downgrades *to* it.
        (SupporterLevel.HOST_3, "top tier"),
        # A drop to NONE is a full loss handled by the grace/revoke messages, not a between-tier downgrade.
        (SupporterLevel.NONE, "loss"),
    ],
)
def test_downgraded_to_rejects_non_downgrade_targets(level: SupporterLevel, match: str):
    with pytest.raises(ValueError, match=match):
        SupporterNotificationMessages.downgraded_to(level)
