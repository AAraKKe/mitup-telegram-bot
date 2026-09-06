"""Tests for the shared datetime conventions.

Every layer reads stored datetimes through these four helpers, so what a naive value means and how
a wall clock crosses into UTC are pinned here rather than at each call site. The tests run under a
non-UTC system timezone where it matters, because reading a naive value as system-local instead of
UTC is the failure these helpers exist to prevent and it is invisible on a UTC machine.
"""

import datetime as dt
import time
from collections.abc import Iterator
from zoneinfo import ZoneInfo

import pytest

from mitup_bot.datetimes import UTC_TIMEZONE, as_utc, in_timezone, local_to_utc, parse_timezone

MADRID = ZoneInfo("Europe/Madrid")
AUCKLAND = ZoneInfo("Pacific/Auckland")

# 22:45 in Madrid on a summer date (UTC+2) and the same instant written bare, as the database
# hands it back.
MOMENT = dt.datetime(2027, 7, 17, 20, 45, tzinfo=dt.UTC)
NAIVE_MOMENT = MOMENT.replace(tzinfo=None)

# The last Sunday in March 2027: the clocks in Madrid jump from 02:00 to 03:00.
SPRING_FORWARD_DATE = dt.date(2027, 3, 28)


@pytest.fixture
def system_timezone_new_york(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the test with the process reading local time as New York.

    `astimezone` on a naive value silently uses the process timezone, so a helper that forgets to
    tag the value passes on a UTC machine and shifts the moment everywhere else.
    """
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_as_utc_tags_a_naive_value():
    assert as_utc(NAIVE_MOMENT) == MOMENT


def test_as_utc_leaves_an_aware_value_on_its_own_offset():
    madrid_value = MOMENT.astimezone(MADRID)

    assert as_utc(madrid_value) is madrid_value


@pytest.mark.usefixtures("system_timezone_new_york")
def test_as_utc_ignores_the_system_timezone():
    assert as_utc(NAIVE_MOMENT) == MOMENT


def test_in_timezone_moves_an_aware_value_onto_the_local_clock():
    assert in_timezone(MOMENT, MADRID).hour == 22


def test_in_timezone_reads_a_naive_value_as_utc():
    assert in_timezone(NAIVE_MOMENT, MADRID) == in_timezone(MOMENT, MADRID)


@pytest.mark.usefixtures("system_timezone_new_york")
def test_in_timezone_ignores_the_system_timezone():
    assert in_timezone(NAIVE_MOMENT, MADRID).hour == 22


def test_in_timezone_can_cross_the_date_line():
    """The date a moment falls on is a statement about the zone it is read in."""
    assert in_timezone(MOMENT, AUCKLAND).date() == dt.date(2027, 7, 18)
    assert in_timezone(MOMENT, dt.UTC).date() == dt.date(2027, 7, 17)


def test_local_to_utc_returns_the_instant_the_local_clocks_show_that_time():
    assert local_to_utc(dt.date(2027, 7, 17), dt.time(22, 45), MADRID) == MOMENT


def test_local_to_utc_replaces_a_timezone_the_time_already_carries():
    """A caller holding a bare wall clock and one that already tagged it land on the same instant."""
    tagged = dt.time(22, 45, tzinfo=AUCKLAND)

    assert local_to_utc(dt.date(2027, 7, 17), tagged, MADRID) == MOMENT


def test_local_to_utc_uses_the_offset_in_force_on_that_date():
    """The offset is resolved against the whole datetime, so a date either side of a DST switch
    converts with its own offset rather than the one in force today."""
    winter = local_to_utc(dt.date(2027, 1, 15), dt.time(12, 0), MADRID)
    summer = local_to_utc(dt.date(2027, 7, 15), dt.time(12, 0), MADRID)

    assert winter.hour == 11
    assert summer.hour == 10


def test_local_to_utc_resolves_a_time_the_local_clocks_skip():
    """02:30 does not exist in Madrid on the day the clocks spring forward. Python reads the gap
    with the offset in force before the switch, which keeps the conversion total."""
    assert local_to_utc(SPRING_FORWARD_DATE, dt.time(2, 30), MADRID) == dt.datetime(2027, 3, 28, 1, 30, tzinfo=dt.UTC)


@pytest.mark.usefixtures("system_timezone_new_york")
def test_local_to_utc_ignores_the_system_timezone():
    assert local_to_utc(dt.date(2027, 7, 17), dt.time(22, 45), MADRID) == MOMENT


def test_parse_timezone_resolves_a_known_zone():
    zone = parse_timezone("Europe/Madrid")

    assert zone is not None
    assert zone.key == "Europe/Madrid"


def test_parse_timezone_returns_none_for_a_zone_the_system_does_not_carry():
    assert parse_timezone("Invalid/Timezone") is None


def test_utc_timezone_names_utc():
    assert UTC_TIMEZONE.key == "UTC"
    assert in_timezone(MOMENT, UTC_TIMEZONE) == MOMENT
