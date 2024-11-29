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
                [Month.APRIL.get(lang=lang), ButtonMessages.GO_FORWARD.get()],
                ["2024", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2024, 4, 7),
            lambda lang: [
                [Month.APRIL.get(lang=lang), ButtonMessages.GO_FORWARD.get()],
                ["2024", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2024, 7, 22),
            lambda lang: [
                [ButtonMessages.GO_BACK.get(), Month.JULY.get(lang=lang), ButtonMessages.GO_FORWARD.get()],
                ["2024", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2024, 3, 12),
            lambda lang: [
                [Month.MARCH.get(lang=lang), ButtonMessages.GO_FORWARD.get()],
                ["2024", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2025, 5, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.MAY.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                [ButtonMessages.GO_BACK.get(), "2025", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2025, 6, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.JUNE.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                [ButtonMessages.GO_BACK.get(), "2025", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2025, 7, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.JULY.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                [ButtonMessages.GO_BACK.get(), "2025", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2024, 12, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.DECEMBER.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                ["2024", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2025, 1, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.JANUARY.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                [ButtonMessages.GO_BACK.get(), "2025", ButtonMessages.GO_FORWARD.get()],
            ],
        ),
        (
            dt.date(2024, 7, 12),
            lambda lang: [
                [
                    ButtonMessages.GO_BACK.get(),
                    Month.JULY.get(lang=lang),
                    ButtonMessages.GO_FORWARD.get(),
                ],
                ["2024", ButtonMessages.GO_FORWARD.get()],
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
