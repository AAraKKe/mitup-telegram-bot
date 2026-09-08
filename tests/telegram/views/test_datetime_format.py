import datetime as dt
import re
from zoneinfo import ZoneInfo

import pytest

from mitup_bot.datetimes import DateFormat, TimeFormat
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.views.datetime_format import (
    DATE_ENTITY_FORMAT,
    DATE_TIME_ENTITY_MAX_LENGTH,
    RANGE_SEPARATOR,
    TIME_ENTITY_FORMAT,
    datetime_content,
    datetime_range_content,
    duration_minutes_content,
    locale_for,
    localized_datetime,
    month_name,
    relative_time_content,
    weekday_names,
)

MADRID = ZoneInfo("Europe/Madrid")
AUCKLAND = ZoneInfo("Pacific/Auckland")
UTC = ZoneInfo("UTC")

# 22:45 in Madrid, the evening before in UTC.
MOMENT = dt.datetime(2027, 3, 17, 21, 45, tzinfo=dt.UTC)
# A creation moment in the same calendar year as MOMENT, and one in the year before it.
CREATED_SAME_YEAR = dt.datetime(2027, 1, 5, 9, 0, tzinfo=dt.UTC)
CREATED_YEAR_BEFORE = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.UTC)

# What every setting off looks like: the shortest date, a 12-hour clock and no zone named.
BARE = TimeFormat(show_timezone=False, clock_24h=False, date_format=DateFormat.DEFAULT)
CLOCK_24H = TimeFormat(show_timezone=False, clock_24h=True, date_format=DateFormat.DEFAULT)
WITH_TIMEZONE = TimeFormat(show_timezone=True, clock_24h=True, date_format=DateFormat.DEFAULT)

# What each shipped language calls the moment, read in Madrid, for a meeting created the same year,
# on a 24-hour clock with the zone named.
LOCALIZED_MOMENT = {
    "en": "Wed, Mar 17, 22:45 Europe/Madrid",
    "es_ES": "mié, 17 mar, 22:45 Europe/Madrid",
    "gl_ES": "mér., 17 de mar., 22:45 Europe/Madrid",
    "de_DE": "Mi., 17. März, 22:45 Europe/Madrid",
    "pt_BR": "qua., 17 de mar. 22:45 Europe/Madrid",
    "it_IT": "mer 17 mar, 22:45 Europe/Madrid",
}

# The same moment for a meeting created the year before, where the year is spelled out.
LOCALIZED_MOMENT_WITH_YEAR = {
    "en": "Wed, Mar 17, 2027, 22:45 Europe/Madrid",
    "es_ES": "mié, 17 mar 2027, 22:45 Europe/Madrid",
    "gl_ES": "mér., 17 de mar. de 2027, 22:45 Europe/Madrid",
    "de_DE": "Mi., 17. März 2027, 22:45 Europe/Madrid",
    "pt_BR": "qua., 17 de mar. de 2027 22:45 Europe/Madrid",
    "it_IT": "mer 17 mar 2027, 22:45 Europe/Madrid",
}


@pytest.mark.parametrize("language", LOCALIZED_MOMENT)
def test_localized_datetime_follows_the_conventions_of_each_language(language: str):
    rendered = localized_datetime(
        MOMENT, lang=language, tz=MADRID, created=CREATED_SAME_YEAR, time_format=WITH_TIMEZONE
    )

    assert rendered == LOCALIZED_MOMENT[language]


@pytest.mark.parametrize("language", LOCALIZED_MOMENT_WITH_YEAR)
def test_localized_datetime_spells_the_year_out_in_each_language(language: str):
    rendered = localized_datetime(
        MOMENT, lang=language, tz=MADRID, created=CREATED_YEAR_BEFORE, time_format=WITH_TIMEZONE
    )

    assert rendered == LOCALIZED_MOMENT_WITH_YEAR[language]


def test_every_shipped_language_has_an_expected_rendering():
    """A language added to the bot must be given its expected datetimes here, not left unchecked."""
    assert sorted(LOCALIZED_MOMENT) == sorted(SUPPORTED_LANGUAGES)
    assert sorted(LOCALIZED_MOMENT_WITH_YEAR) == sorted(SUPPORTED_LANGUAGES)


def test_english_formats_as_us_english():
    """English is CLDR's `en`, which puts the month first and offers a 12-hour clock."""
    rendered = localized_datetime(MOMENT, lang="en", tz=MADRID, created=CREATED_SAME_YEAR, time_format=BARE)

    assert rendered == "Wed, Mar 17, 10:45 PM"


@pytest.mark.parametrize(
    "date_format,expected",
    [
        (DateFormat.DEFAULT, "Wed, Mar 17, 10:45 PM"),
        (DateFormat.LONG, "March 17, 2027, 10:45 PM"),
        (DateFormat.FULL, "Wednesday, March 17, 2027, 10:45 PM"),
    ],
    ids=["default", "long", "full"],
)
def test_each_date_format_writes_the_date_out_to_its_own_length_in_english(date_format: DateFormat, expected: str):
    time_format = TimeFormat(show_timezone=False, clock_24h=False, date_format=date_format)

    rendered = localized_datetime(MOMENT, lang="en", tz=MADRID, created=CREATED_SAME_YEAR, time_format=time_format)

    assert rendered == expected


@pytest.mark.parametrize(
    "date_format,expected",
    [
        (DateFormat.DEFAULT, "mié, 17 mar, 22:45"),
        (DateFormat.LONG, "17 de marzo de 2027, 22:45"),
        (DateFormat.FULL, "miércoles, 17 de marzo de 2027, 22:45"),
    ],
    ids=["default", "long", "full"],
)
def test_each_date_format_writes_the_date_out_to_its_own_length_in_spanish(date_format: DateFormat, expected: str):
    time_format = TimeFormat(show_timezone=False, clock_24h=True, date_format=date_format)

    rendered = localized_datetime(MOMENT, lang="es_ES", tz=MADRID, created=CREATED_SAME_YEAR, time_format=time_format)

    assert rendered == expected


@pytest.mark.parametrize("language", ["en", "es_ES"])
@pytest.mark.parametrize("date_format", list(DateFormat))
def test_the_longer_date_formats_always_carry_the_year(language: str, date_format: DateFormat):
    """The two CLDR presets spell the year out whatever the meeting was created in, so only the
    default format has a short form to fall back to."""
    time_format = TimeFormat(show_timezone=False, clock_24h=True, date_format=date_format)

    rendered = localized_datetime(MOMENT, lang=language, tz=MADRID, created=CREATED_SAME_YEAR, time_format=time_format)

    assert ("2027" in rendered) == (date_format is not DateFormat.DEFAULT)


@pytest.mark.parametrize("language", ["en", "es_ES"])
def test_the_clock_setting_picks_between_a_24_hour_and_a_12_hour_time(language: str):
    on_24h = localized_datetime(MOMENT, lang=language, tz=MADRID, created=None, time_format=CLOCK_24H)
    on_12h = localized_datetime(MOMENT, lang=language, tz=MADRID, created=None, time_format=BARE)

    assert "22:45" in on_24h
    assert "22:45" not in on_12h
    assert "10:45" in on_12h


@pytest.mark.parametrize("language", ["en", "es_ES"])
def test_the_timezone_setting_names_the_zone_by_its_own_id_or_says_nothing(language: str):
    """The zone id names the same place in every language, unlike a CLDR abbreviation, which some
    zones have no name for at all."""
    shown = localized_datetime(MOMENT, lang=language, tz=MADRID, created=None, time_format=WITH_TIMEZONE)
    hidden = localized_datetime(MOMENT, lang=language, tz=MADRID, created=None, time_format=CLOCK_24H)

    assert shown == f"{hidden} Europe/Madrid"


def test_the_named_zone_is_the_one_the_moment_is_read_in():
    auckland = localized_datetime(MOMENT, lang="es_ES", tz=AUCKLAND, created=None, time_format=WITH_TIMEZONE)

    assert auckland.endswith("Pacific/Auckland")


def test_localized_datetime_moves_a_stored_utc_moment_into_the_given_timezone():
    """The same instant is a different wall clock, and a different day, on the other side of it."""
    in_utc = localized_datetime(MOMENT, lang="es_ES", tz=UTC, created=CREATED_SAME_YEAR, time_format=CLOCK_24H)
    in_auckland = localized_datetime(
        MOMENT, lang="es_ES", tz=AUCKLAND, created=CREATED_SAME_YEAR, time_format=CLOCK_24H
    )

    assert in_utc == "mié, 17 mar, 21:45"
    assert in_auckland == "jue, 18 mar, 10:45"


def test_the_year_is_decided_in_the_display_timezone():
    """A creation moment half an hour into the new year in Madrid is still the old year in UTC, and
    the reader only ever sees the timezone the meeting is shown in."""
    created = dt.datetime(2026, 12, 31, 23, 30, tzinfo=dt.UTC)
    value = dt.datetime(2027, 6, 1, 10, 0, tzinfo=dt.UTC)

    assert "2027" not in localized_datetime(value, lang="es_ES", tz=MADRID, created=created, time_format=CLOCK_24H)
    assert "2027" in localized_datetime(value, lang="es_ES", tz=UTC, created=created, time_format=CLOCK_24H)


def test_localized_datetime_reads_naive_values_as_utc():
    """Meeting datetimes and creation timestamps both come out of the database without a tzinfo."""
    rendered = localized_datetime(
        MOMENT.replace(tzinfo=None),
        lang="es_ES",
        tz=MADRID,
        created=CREATED_YEAR_BEFORE.replace(tzinfo=None),
        time_format=WITH_TIMEZONE,
    )

    assert rendered == LOCALIZED_MOMENT_WITH_YEAR["es_ES"]


def test_a_meeting_with_no_creation_moment_keeps_the_short_form():
    """An unstored meeting has no creation timestamp yet, and the year it is created in is the year
    it is almost always scheduled in."""
    rendered = localized_datetime(MOMENT, lang="es_ES", tz=MADRID, created=None, time_format=WITH_TIMEZONE)

    assert rendered == LOCALIZED_MOMENT["es_ES"]


def test_localized_datetime_falls_back_to_english_for_a_language_babel_does_not_know():
    unknown = localized_datetime(MOMENT, lang="zz_ZZ", tz=MADRID, created=CREATED_SAME_YEAR, time_format=WITH_TIMEZONE)
    malformed = localized_datetime(
        MOMENT, lang="not a locale", tz=MADRID, created=CREATED_SAME_YEAR, time_format=WITH_TIMEZONE
    )

    assert unknown == LOCALIZED_MOMENT["en"]
    assert malformed == LOCALIZED_MOMENT["en"]


def test_locale_for_reads_both_spellings_of_a_locale():
    assert locale_for("es-ES") == locale_for("es_ES")


def test_datetime_content_reads_as_the_localized_text():
    content = datetime_content(MOMENT, lang="es_ES", tz=MADRID, created=CREATED_SAME_YEAR, time_format=WITH_TIMEZONE)

    assert content.text == LOCALIZED_MOMENT["es_ES"]


def test_datetime_content_puts_the_date_and_the_time_in_their_own_entities():
    content = datetime_content(MOMENT, lang="es_ES", tz=MADRID, created=CREATED_SAME_YEAR, time_format=WITH_TIMEZONE)

    unix = int(MOMENT.timestamp())
    assert content.html == (
        f'mié, <tg-time unix="{unix}" format="{DATE_ENTITY_FORMAT}">17 mar</tg-time>, '
        f'<tg-time unix="{unix}" format="{TIME_ENTITY_FORMAT}">22:45</tg-time> Europe/Madrid'
    )


def test_the_weekday_stays_outside_the_date_entity_in_the_full_format():
    full = TimeFormat(show_timezone=False, clock_24h=True, date_format=DateFormat.FULL)

    content = datetime_content(MOMENT, lang="es_ES", tz=MADRID, created=CREATED_SAME_YEAR, time_format=full)

    assert content.html.startswith("miércoles, <tg-time")
    assert ">17 de marzo de 2027</tg-time>" in content.html


@pytest.mark.parametrize("language", ["en", "es_ES", "gl_ES", "de_DE", "pt_BR", "it_IT"])
@pytest.mark.parametrize("date_format", list(DateFormat))
@pytest.mark.parametrize("clock_24h", [True, False])
def test_the_entity_text_never_runs_past_what_telegram_accepts(language: str, date_format: DateFormat, clock_24h: bool):
    """Telegram refuses the whole message when a `date_time` entity text is longer than the cap."""
    time_format = TimeFormat(show_timezone=True, clock_24h=clock_24h, date_format=date_format)
    content = datetime_content(
        MOMENT, lang=language, tz=ZoneInfo("America/Argentina/Buenos_Aires"), created=None, time_format=time_format
    )

    entity_texts = re.findall(r"<tg-time[^>]*>(.*?)</tg-time>", content.html)
    assert len(entity_texts) == 2
    assert all(len(text) <= DATE_TIME_ENTITY_MAX_LENGTH for text in entity_texts)


# --- A span between two moments ---

# Half an hour after MOMENT, still on the same evening in Madrid.
SAME_EVENING = MOMENT + dt.timedelta(minutes=30)
# Ninety minutes after MOMENT: past midnight in Madrid, still the same day in UTC.
PAST_MIDNIGHT_IN_MADRID = MOMENT + dt.timedelta(hours=1, minutes=30)

# The span from MOMENT to SAME_EVENING in each shipped language, read in Madrid with the zone named.
LOCALIZED_SPAN = {
    "en": "Wed, Mar 17, 22:45 - 23:15 Europe/Madrid",
    "es_ES": "mié, 17 mar, 22:45 - 23:15 Europe/Madrid",
    "gl_ES": "mér., 17 de mar., 22:45 - 23:15 Europe/Madrid",
    "de_DE": "Mi., 17. März, 22:45 - 23:15 Europe/Madrid",
    "pt_BR": "qua., 17 de mar. 22:45 - 23:15 Europe/Madrid",
    "it_IT": "mer 17 mar, 22:45 - 23:15 Europe/Madrid",
}


def span(
    start: dt.datetime, end: dt.datetime, *, lang: str = "es_ES", tz: ZoneInfo = MADRID, time_format=WITH_TIMEZONE
):
    return datetime_range_content(start, end, lang=lang, tz=tz, created=CREATED_SAME_YEAR, time_format=time_format)


@pytest.mark.parametrize("language", LOCALIZED_SPAN)
def test_a_span_within_one_day_names_the_date_once_then_both_times(language: str):
    assert span(MOMENT, SAME_EVENING, lang=language).text == LOCALIZED_SPAN[language]


def test_every_shipped_language_has_an_expected_span():
    assert set(LOCALIZED_SPAN) == set(SUPPORTED_LANGUAGES)


def test_a_span_crossing_midnight_writes_both_moments_out_and_names_the_zone_once():
    text = span(MOMENT, PAST_MIDNIGHT_IN_MADRID).text

    assert text == f"mié, 17 mar, 22:45{RANGE_SEPARATOR}jue, 18 mar, 0:15 Europe/Madrid"


def test_whether_a_span_stays_within_a_day_is_judged_in_the_display_timezone():
    assert span(MOMENT, PAST_MIDNIGHT_IN_MADRID, tz=UTC).text == f"mié, 17 mar, 21:45{RANGE_SEPARATOR}23:15 UTC"


def test_a_span_puts_the_date_and_each_time_in_their_own_entities():
    html = span(MOMENT, SAME_EVENING, time_format=CLOCK_24H).html

    start, end = int(MOMENT.timestamp()), int(SAME_EVENING.timestamp())
    assert html == (
        f'mié, <tg-time unix="{start}" format="{DATE_ENTITY_FORMAT}">17 mar</tg-time>, '
        f'<tg-time unix="{start}" format="{TIME_ENTITY_FORMAT}">22:45</tg-time>{RANGE_SEPARATOR}'
        f'<tg-time unix="{end}" format="{TIME_ENTITY_FORMAT}">23:15</tg-time>'
    )


def test_a_span_across_days_carries_a_date_entity_for_each_end():
    html = span(MOMENT, PAST_MIDNIGHT_IN_MADRID, time_format=CLOCK_24H).html

    end = int(PAST_MIDNIGHT_IN_MADRID.timestamp())
    assert html.count(f'format="{DATE_ENTITY_FORMAT}"') == 2
    assert f'<tg-time unix="{end}" format="{DATE_ENTITY_FORMAT}">18 mar</tg-time>' in html


def test_a_span_with_no_zone_shown_ends_on_the_last_time():
    assert (
        span(MOMENT, SAME_EVENING, lang="en", time_format=BARE).text
        == f"Wed, Mar 17, 10:45 PM{RANGE_SEPARATOR}11:15 PM"
    )


# The header row the calendar draws in each shipped language, Monday first.
WEEKDAY_HEADERS = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "es_ES": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
    "gl_ES": ["luns", "mar.", "mér.", "xov.", "ven.", "sáb.", "dom."],
    "de_DE": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "pt_BR": ["seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom."],
    "it_IT": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
}

# What each shipped language calls June on the calendar's month row. The lowercase spellings are
# what CLDR gives for a month standing on its own, not a capitalization the code failed to apply.
JUNE = {
    "en": "June",
    "es_ES": "junio",
    "gl_ES": "xuño",
    "de_DE": "Juni",
    "pt_BR": "junho",
    "it_IT": "giugno",
}


@pytest.mark.parametrize("language", WEEKDAY_HEADERS)
def test_weekday_names_read_monday_to_sunday_in_each_language(language: str):
    assert weekday_names(language) == WEEKDAY_HEADERS[language]


@pytest.mark.parametrize("language", JUNE)
def test_month_name_keeps_the_capitalization_the_language_writes(language: str):
    assert month_name(6, language) == JUNE[language]


def test_month_name_counts_months_the_way_a_date_does():
    assert month_name(1, "en") == "January"
    assert month_name(12, "en") == "December"


def test_every_shipped_language_has_expected_calendar_names():
    """A language added to the bot must be given its expected weekdays and months here."""
    assert sorted(WEEKDAY_HEADERS) == sorted(SUPPORTED_LANGUAGES)
    assert sorted(JUNE) == sorted(SUPPORTED_LANGUAGES)


def test_calendar_names_fall_back_to_english_for_a_language_babel_does_not_know():
    assert weekday_names("zz_ZZ") == WEEKDAY_HEADERS["en"]
    assert month_name(6, "zz_ZZ") == JUNE["en"]


# --- How far a moment is from now ---

# An hour before MOMENT, so a distance counted from here lands on a round 60 minutes.
NOW = MOMENT - dt.timedelta(hours=1)

# How each shipped language puts a start that is 25 minutes away.
RELATIVE_DISTANCE = {
    "en": "in 25 minutes",
    "es_ES": "dentro de 25 minutos",
    "gl_ES": "en 25 minutos",
    "de_DE": "in 25 Minuten",
    "pt_BR": "em 25 minutos",
    "it_IT": "tra 25 minuti",
}


@pytest.mark.parametrize(
    "gap, expected",
    [(dt.timedelta(minutes=25), "in 25 minutes"), (dt.timedelta(minutes=-5), "5 minutes ago")],
    ids=["ahead", "behind"],
)
def test_relative_time_says_which_side_of_now_a_moment_falls_on(gap: dt.timedelta, expected: str):
    assert relative_time_content(NOW + gap, now=NOW, lang="en").text == expected


@pytest.mark.parametrize("language", RELATIVE_DISTANCE)
def test_relative_time_is_written_the_way_each_language_writes_it(language: str):
    assert (
        relative_time_content(NOW + dt.timedelta(minutes=25), now=NOW, lang=language).text
        == (RELATIVE_DISTANCE[language])
    )


def test_every_shipped_language_has_an_expected_distance():
    """A language added to the bot must be given its expected wording here, not left unchecked."""
    assert sorted(RELATIVE_DISTANCE) == sorted(SUPPORTED_LANGUAGES)


def test_relative_time_is_plain_text_rather_than_a_time_chip():
    """The moment itself sits on the same card as a tappable time; the distance only reads."""
    html = relative_time_content(MOMENT, now=NOW, lang="en").html

    assert html == "in 1 hour"


def test_relative_time_reads_a_stored_moment_and_a_stored_clock_as_utc():
    """Datetimes come out of the database naive, so a distance between two of them is the same one
    an aware pair gives."""
    naive = relative_time_content(MOMENT.replace(tzinfo=None), now=NOW.replace(tzinfo=None), lang="en")

    assert naive.text == relative_time_content(MOMENT, now=NOW, lang="en").text


def test_relative_time_falls_back_to_english_for_a_language_babel_does_not_know():
    assert relative_time_content(NOW + dt.timedelta(minutes=25), now=NOW, lang="zz_ZZ").text == "in 25 minutes"


# How each shipped language names a single minute and a run of them.
MINUTE_COUNTS = {
    "en": ("1 minute", "25 minutes"),
    "es_ES": ("1 minuto", "25 minutos"),
    "gl_ES": ("1 minuto", "25 minutos"),
    "de_DE": ("1 Minute", "25 Minuten"),
    "pt_BR": ("1 minuto", "25 minutos"),
    "it_IT": ("1 minuto", "25 minuti"),
}


@pytest.mark.parametrize("language", MINUTE_COUNTS)
def test_a_count_of_minutes_carries_the_unit_each_language_spells_for_it(language: str):
    singular, plural = MINUTE_COUNTS[language]

    assert duration_minutes_content(1, lang=language).text == singular
    assert duration_minutes_content(25, lang=language).text == plural


def test_every_shipped_language_has_an_expected_minute_count():
    """A language added to the bot must be given its expected wording here, not left unchecked."""
    assert sorted(MINUTE_COUNTS) == sorted(SUPPORTED_LANGUAGES)


def test_a_count_of_minutes_falls_back_to_english_for_a_language_babel_does_not_know():
    assert duration_minutes_content(1, lang="zz_ZZ").text == "1 minute"
