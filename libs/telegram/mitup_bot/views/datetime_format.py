from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

from babel import Locale, UnknownLocaleError
from babel.dates import format_date, format_skeleton, format_timedelta
from babel.units import format_unit

from mitup_bot.datetimes import DateFormat, TimeFormat, as_utc, in_timezone
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.entities import EntityDateTime
from mitup_bot.utils.rich_message import RichContent, date_time_content
from mitup_bot.utils.rich_template import render_rich

# CLDR skeletons for the date half under `DateFormat.DEFAULT`: abbreviated weekday, day of month,
# abbreviated month, and the same with the year. The year is spelled out only when the meeting
# falls outside the year it was created in, so the everyday case stays short enough to share a line
# with the time. The two longer formats are CLDR date presets, which always carry the year.
DATE_SKELETON = "MMMEd"
DATE_SKELETON_WITH_YEAR = "yMMMEd"
DAY_SKELETON = "MMMd"
DAY_SKELETON_WITH_YEAR = "yMMMd"
CLDR_DATE_PRESETS = {DateFormat.LONG: "long", DateFormat.FULL: "full"}

# CLDR skeletons for the time half, on a 24-hour clock and on a 12-hour one.
TIME_SKELETON_24H = "Hm"
TIME_SKELETON_12H = "hm"

# Telegram's `date_time` entity format tokens: the date half of the moment and the time half.
DATE_ENTITY_FORMAT = "D"
TIME_ENTITY_FORMAT = "T"
# Telegram refuses a `date_time` entity whose text runs past this many characters.
DATE_TIME_ENTITY_MAX_LENGTH = 31
# Stand in for the two halves while the locale's own pattern places them.
TIME_MARKER = "\x00"
DATE_MARKER = "\x01"
# Sits between the two ends of a span: a plain hyphen, since the copy uses no dashes.
RANGE_SEPARATOR = " - "

# CLDR names a weekday or month differently inside a date ("format") and on its own ("stand-alone").
CALENDAR_NAME_CONTEXT = "stand-alone"


def locale_for(lang: str) -> Locale:
    """The CLDR locale backing *lang*, falling back to English for anything Babel does not know.

    Both spellings of a locale resolve to the same data: stored languages are underscored
    ("es_ES") while a tag taken straight from a Telegram client is hyphenated ("es-ES").
    """
    try:
        return Locale.parse(lang.replace("-", "_"))
    except UnknownLocaleError, ValueError:
        return Locale.parse(TranslationEngine.FALLBACK_LANG)


def shows_year(local_value: dt.datetime, created: dt.datetime | None, tz: dt.tzinfo) -> bool:
    """Whether a moment already read in *tz* needs its year spelled out next to *created*.

    Both years are read in the display timezone, since a moment sits on either side of New Year
    depending on the zone it is read in and the reader only ever sees the one. A meeting with no
    creation moment yet reads as the same year, which keeps an unstored meeting on the short form.
    """
    if created is None:
        return False
    return in_timezone(created, tz).year != local_value.year


def date_texts(
    local_value: dt.datetime, *, locale: Locale, created: dt.datetime | None, tz: dt.tzinfo, date_format: DateFormat
) -> tuple[str, str]:
    """The date half of a moment already read in *tz* as *date_format* writes it, and the part of it
    without the weekday, which is what the date entity carries."""
    if (preset := CLDR_DATE_PRESETS.get(date_format)) is not None:
        whole = format_date(local_value, format=preset, locale=locale)
        return whole, format_date(local_value, format=CLDR_DATE_PRESETS[DateFormat.LONG], locale=locale)
    with_year = shows_year(local_value, created, tz)
    whole = format_skeleton(DATE_SKELETON_WITH_YEAR if with_year else DATE_SKELETON, local_value, locale=locale)
    core = format_skeleton(DAY_SKELETON_WITH_YEAR if with_year else DAY_SKELETON, local_value, locale=locale)
    return whole, core


def datetime_pieces(
    value: dt.datetime, *, lang: str, tz: ZoneInfo, created: dt.datetime | None, time_format: TimeFormat
) -> tuple[Locale, str, str, str, str]:
    """The locale of *lang* and the date, the date without its weekday, the time and the zone texts
    of *value* read in *tz*."""
    locale = locale_for(lang)
    local_value = in_timezone(value, tz)
    date, day = date_texts(local_value, locale=locale, created=created, tz=tz, date_format=time_format.date_format)
    clock = TIME_SKELETON_24H if time_format.clock_24h else TIME_SKELETON_12H
    time = format_skeleton(clock, local_value, locale=locale)
    zone = f" {tz.key}" if time_format.show_timezone else ""
    return locale, date, day, time, zone


def localized_datetime(
    value: dt.datetime, *, lang: str, tz: ZoneInfo, created: dt.datetime | None, time_format: TimeFormat
) -> str:
    """Write *value* out as plain text in *tz*, following the conventions of *lang* through CLDR.

    The zone is named by its own id rather than a CLDR abbreviation, which exists for only some
    zones in each language. *created* decides whether the year is shown.
    """
    locale, date, _, time, zone = datetime_pieces(value, lang=lang, tz=tz, created=created, time_format=time_format)
    return str(locale.datetime_formats["short"]).format(f"{time}{zone}", date)


def date_content(value: dt.datetime, date: str, day: str) -> RichContent:
    """*date* with *day*, the part of it a date entity may carry, inside the entity."""
    weekday, _, rest = date.partition(day)
    return render_rich(t"{weekday}{EntityDateTime(day, value, DATE_ENTITY_FORMAT)}{rest}")


def time_content(value: dt.datetime, time: str) -> RichContent:
    return date_time_content(EntityDateTime(time, value, TIME_ENTITY_FORMAT))


def placed_content(locale: Locale, date: RichContent, time: RichContent) -> RichContent:
    """*date* and *time* in the order the locale's own short pattern puts them, with its separators."""
    content = RichContent()
    for part in re.split(
        f"({TIME_MARKER}|{DATE_MARKER})", str(locale.datetime_formats["short"]).format(TIME_MARKER, DATE_MARKER)
    ):
        if part == DATE_MARKER:
            content = content.append(date)
        elif part == TIME_MARKER:
            content = content.append(time)
        else:
            content = content.append(part)
    return content


def datetime_content(
    value: dt.datetime, *, lang: str, tz: ZoneInfo, created: dt.datetime | None, time_format: TimeFormat
) -> RichContent:
    """The same text as `localized_datetime`, the date and the time each inside a `date_time` entity.

    The weekday and the zone stay plain text: Telegram refuses an entity text longer than
    `DATE_TIME_ENTITY_MAX_LENGTH`, which a full date or a zone id runs past.
    """
    locale, date, day, time, zone = datetime_pieces(value, lang=lang, tz=tz, created=created, time_format=time_format)
    return placed_content(locale, date_content(value, date, day), time_content(value, time).append(zone))


def datetime_range_content(
    start: dt.datetime,
    end: dt.datetime,
    *,
    lang: str,
    tz: ZoneInfo,
    created: dt.datetime | None,
    time_format: TimeFormat,
) -> RichContent:
    """The span from *start* to *end* as one line, each date and time inside its own entity.

    A span within one day names the date once and then both times; one crossing midnight writes
    both moments out in full. The zone, when the format shows it, closes the line.
    """
    locale, date, day, start_time, zone = datetime_pieces(
        start, lang=lang, tz=tz, created=created, time_format=time_format
    )
    _, end_date, end_day, end_time, _ = datetime_pieces(end, lang=lang, tz=tz, created=created, time_format=time_format)
    times = time_content(start, start_time).append(RANGE_SEPARATOR).append(time_content(end, end_time))
    if in_timezone(start, tz).date() == in_timezone(end, tz).date():
        return placed_content(locale, date_content(start, date, day), times.append(zone))
    first = placed_content(locale, date_content(start, date, day), time_content(start, start_time))
    last = placed_content(locale, date_content(end, end_date, end_day), time_content(end, end_time).append(zone))
    return first.append(RANGE_SEPARATOR).append(last)


def relative_time_content(moment: dt.datetime, *, now: dt.datetime, lang: str) -> RichContent:
    """How far *moment* sits from *now* in the words of *lang* ("in 25 minutes", "5 minutes ago").

    Plain text, written when the message is sent: the moment itself is on the same card as a
    tappable time, so the distance is not one.
    """
    gap = as_utc(moment) - as_utc(now)
    return RichContent(format_timedelta(gap, add_direction=True, granularity="minute", locale=locale_for(lang)))


def duration_minutes_content(minutes: int, *, lang: str) -> RichContent:
    """A count of minutes with its unit spelled the way *lang* spells it ("1 minute", "25 minutes")."""
    return RichContent(format_unit(minutes, "duration-minute", length="long", locale=locale_for(lang)))


def weekday_names(lang: str) -> list[str]:
    """Abbreviated, Monday first."""
    names = locale_for(lang).days[CALENDAR_NAME_CONTEXT]["abbreviated"]
    return [names[weekday] for weekday in range(7)]


def month_name(month: int, lang: str) -> str:
    return locale_for(lang).months[CALENDAR_NAME_CONTEXT]["wide"][month]
