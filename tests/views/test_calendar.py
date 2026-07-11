import calendar
import datetime as dt
from collections.abc import Callable

import freezegun
import pytest

from mitup_bot.callback_data import DateCallbackData
from mitup_bot.utils import Emojis
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, Month
from mitup_bot.views.calendar import CalendarKeyboard
from tests.helpers.calendar_july_2024 import calendar_markup


@freezegun.freeze_time("2024-06-12", tz_offset=0)
def test_full_calendar_markup(lang: str):
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 6, 12),
        current_date=dt.date(2024, 7, 15),
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
        lang=lang,
    )

    assert cal.keyboard == calendar_markup(lang)


@pytest.mark.parametrize(
    "current_date, expected_text",
    [
        (
            dt.date(2024, 4, 22),
            lambda lang: [
                [Month.APRIL.get_text(lang=lang), ButtonMessages.GO_FORWARD.get_text()],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2024, 4, 7),
            lambda lang: [
                [Month.APRIL.get_text(lang=lang), ButtonMessages.GO_FORWARD.get_text()],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2024, 7, 22),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.JULY.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2024, 3, 12),
            lambda lang: [
                [Month.MARCH.get_text(lang=lang), ButtonMessages.GO_FORWARD.get_text()],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2025, 5, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.MAY.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                [ButtonMessages.GO_BACK.get_text(), "2025", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2025, 6, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.JUNE.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                [ButtonMessages.GO_BACK.get_text(), "2025", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2025, 7, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.JULY.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                [ButtonMessages.GO_BACK.get_text(), "2025", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2024, 12, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.DECEMBER.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2025, 1, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.JANUARY.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                [ButtonMessages.GO_BACK.get_text(), "2025", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
        (
            dt.date(2024, 7, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get_text(),
                    Month.JULY.get_text(lang=lang),
                    ButtonMessages.GO_FORWARD.get_text(),
                ],
                ["2024", ButtonMessages.GO_FORWARD.get_text()],
            ],
        ),
    ],
    ids=[
        "future_day_current_month",
        "past_day_current_month",
        "future_month_same_year",
        "past_month_same_year",
        "previous_month_next_year",
        "same_month_next_year",
        "next_month_next_year",
        "last_month_of_the_year",
        "first_month_of_the_year",
        "anchor_and_current_in_the_future",
    ],
)
@freezegun.freeze_time("2024-04-12", tz_offset=0)
def test_calendar_navigation_buttons(lang: str, current_date: dt.date, expected_text: Callable[[str], list[list[str]]]):
    # Given taht the anchor date is 2024-06-12
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 6, 12),
        current_date=current_date,
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
        lang=lang,
    )

    # Navigations buttons are after the calendar rows
    n_rows = len(calendar.Calendar().monthdayscalendar(current_date.year, current_date.month))
    navigation_rows = cal.keyboard[n_rows + 1 :]
    actual_text = [[button.text for button in row] for row in navigation_rows]

    assert expected_text(lang) == actual_text


@freezegun.freeze_time("2024-06-12", tz_offset=0)
def test_callback_in_buttons():
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 6, 12),
        current_date=dt.date(2025, 7, 15),
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
    )

    # Weekdays should all be empty
    assert [button.callback_data for button in cal.keyboard[0]] == [cb.EMPTY] * 7

    # Days of the month should have the callback_data with the date
    # Add three empty callbacks to the first three days of the month and the last one
    #      July 2027
    # Mo Tu We Th Fr Sa Su
    #           1  2  3  4
    #  5  6  7  8  9 10 11
    # 12 13 14 15 16 17 18
    # 19 20 21 22 23 24 25
    # 26 27 28 29 30 31
    expected_callbacks = [
        cb.EMPTY if day.month != 7 else DateCallbackData(entity="cal", action="go", id=1).with_date(day)
        for week in calendar.Calendar().monthdatescalendar(2025, 7)
        for day in week
    ]
    assert expected_callbacks == [button.callback_data for row in cal.keyboard[1:-2] for button in row]

    # Navigation buttons should be full set for this month
    expected_callbacks = [
        DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2025, 6, 12)),
        cb.EMPTY,
        DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2025, 8, 12)),
        DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2024, 7, 12)),
        cb.EMPTY,
        DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2026, 7, 12)),
    ]
    assert expected_callbacks == [button.callback_data for row in cal.keyboard[-2:] for button in row]


def test_calendar_marks_anchor_date():
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 7, 12),
        current_date=dt.date(2024, 7, 12),
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
    )

    # Check should be the fifth element of the second month row
    #      July 2024
    # Mo Tu We Th Fr Sa Su
    #  1  2  3  4  5  6  7
    #  8  9 10 11  x 13 14
    # 15 16 17 18 19 20 21
    # 22 23 24 25 26 27 28
    # 29 30 31
    assert cal.keyboard[2][4].text == Emojis.CHECK.value


@freezegun.freeze_time("2024-01-01", tz_offset=0)
def test_navigation_day_clamps_to_last_valid_day_of_month():
    # anchor_date is Jan 31. Navigating forward one month leads to Feb, which has no day 31.
    # The __navigation_day fallback must return the last day of Feb (29 in 2024, because it's a leap year).
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 1, 31),
        current_date=dt.date(2024, 1, 31),
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
    )

    # The forward navigation button (by month) should target 2024-02-29 (2024 is a leap year)
    # Navigation rows are the last two rows; the month-nav row is at index -2
    month_nav_row = cal.keyboard[-2]
    # There should be at least a forward button (the label and forward buttons)
    # The rightmost button in the row is always the forward button
    forward_button = month_nav_row[-1]
    forward_cb = forward_button.callback_data
    assert isinstance(forward_cb, DateCallbackData)
    # 2024 is a leap year — Feb has 29 days, so the clamped day is 29
    assert forward_cb.date == dt.date(2024, 2, 29)  # last valid day in Feb 2024


def test_calendar_str_contains_pipe_separator():
    cal = CalendarKeyboard(
        anchor_date=dt.date(2024, 7, 12),
        current_date=dt.date(2024, 7, 12),
        callback_data=DateCallbackData(entity="cal", action="go", id=1),
        navigation_callback_data=DateCallbackData(entity="nav", action="go", id=1),
    )
    result = str(cal)
    assert isinstance(result, str)
    assert len(result) > 0
    assert "|" in result
