import logging

import pytest
from pydantic import ValidationError
from pytest import LogCaptureFixture

from mitup_bot.datetimes import DateFormat, TimeFormat
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Settings


def test_valid_timezone(settings: Settings):
    assert settings.tz.key == "Europe/Madrid"


def test_invalid_timezone(settings: Settings, caplog: LogCaptureFixture):
    settings.timezone = "Invalid/Timezone"
    with caplog.at_level(logging.WARNING):
        assert settings.tz.key == "UTC"
        assert "Invalid timezone" in caplog.records[0].message


@pytest.mark.parametrize(
    "timeout",
    [LifecyclePolicy.get().max_timeout_minutes + 1, 99_999_999_999],
    ids=["just_above_cap", "far_above_cap"],
)
def test_timeout_above_cap_is_rejected_on_assignment(settings: Settings, timeout: int):
    """The cap belongs to the model, so it holds on any write path: an over-cap timeout keeps its
    owner's dated meetings active forever."""
    with pytest.raises(ValidationError):
        settings.timeout = timeout


def test_timeout_above_cap_is_rejected_on_construction():
    with pytest.raises(ValidationError):
        Settings(timeout=LifecyclePolicy.get().max_timeout_minutes + 1)


def test_timeout_at_cap_is_accepted(settings: Settings):
    settings.timeout = LifecyclePolicy.get().max_timeout_minutes

    assert settings.timeout == LifecyclePolicy.get().max_timeout_minutes


def test_a_new_settings_row_names_the_time_format_every_meeting_starts_with():
    assert Settings().default_time_format == TimeFormat(
        show_timezone=False, clock_24h=True, date_format=DateFormat.DEFAULT
    )


def test_the_default_time_format_reads_the_three_stored_defaults(settings: Settings):
    settings.default_show_timezone = False
    settings.default_clock_24h = False
    settings.default_date_format = DateFormat.FULL

    assert settings.default_time_format == TimeFormat(show_timezone=False, clock_24h=False, date_format=DateFormat.FULL)
