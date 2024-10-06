"""
We want to have at least a test calendar to validate the entire markup.
Since this is gigantic we are keeping it here in a separate file.

The calendare represents the markup as request with anchor date being June 12th, 2024
and the current date being July 16th, 2024.

Since we are in 2024, July 16th, the calendar will have the following structure:
- The first row will have the weekdays from Monday to Sunday.
- The second row will have the days from 1 to 7.
- The third row will have the days from 8 to 14.
- The fourth row will have the days from 15 to 21.
- The fifth row will have the days from 22 to 28.
- The sixth row will have the days from 29 to 31, filling with empty spaces the days left.
- The seventh row will have the navigation buttons for the month. Going back and forward
- The eighth row will have the navigation buttons for the year. Cannot go back but we can go forward

This is how the calendar would look like for July 2024.

     July 2024
Mo Tu We Th Fr Sa Su
 1  2  3  4  5  6  7
 8  9 10 11 12 13 14
15 16 17 18 19 20 21
22 23 24 25 26 27 28
29 30 31
"""

import datetime as dt

from mitup_bot.callback_data import DateCallbackData
from mitup_bot.utils import ButtonMessages, MonthList, Weekday
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig


def calendar_markup(lang: str) -> list[list[ButtonConfig]]:
    # Weekdays
    return [
        [
            ButtonConfig(text=Weekday.MONDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.TUESDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.WEDNESDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.THURSDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.FRIDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.SATURDAY.get(lang=lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.SUNDAY.get(lang=lang), callback_data=cb.EMPTY),
        ],
        # Actual calendar
        [
            ButtonConfig(
                text="1",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 1)),
            ),
            ButtonConfig(
                text="2",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 2)),
            ),
            ButtonConfig(
                text="3",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 3)),
            ),
            ButtonConfig(
                text="4",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 4)),
            ),
            ButtonConfig(
                text="5",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 5)),
            ),
            ButtonConfig(
                text="6",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 6)),
            ),
            ButtonConfig(
                text="7",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 7)),
            ),
        ],
        [
            ButtonConfig(
                text="8",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 8)),
            ),
            ButtonConfig(
                text="9",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 9)),
            ),
            ButtonConfig(
                text="10",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 10)),
            ),
            ButtonConfig(
                text="11",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 11)),
            ),
            ButtonConfig(
                text="12",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 12)),
            ),
            ButtonConfig(
                text="13",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 13)),
            ),
            ButtonConfig(
                text="14",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 14)),
            ),
        ],
        [
            ButtonConfig(
                text="15",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 15)),
            ),
            ButtonConfig(
                text="16",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 16)),
            ),
            ButtonConfig(
                text="17",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 17)),
            ),
            ButtonConfig(
                text="18",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 18)),
            ),
            ButtonConfig(
                text="19",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 19)),
            ),
            ButtonConfig(
                text="20",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 20)),
            ),
            ButtonConfig(
                text="21",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 21)),
            ),
        ],
        [
            ButtonConfig(
                text="22",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 22)),
            ),
            ButtonConfig(
                text="23",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 23)),
            ),
            ButtonConfig(
                text="24",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 24)),
            ),
            ButtonConfig(
                text="25",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 25)),
            ),
            ButtonConfig(
                text="26",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 26)),
            ),
            ButtonConfig(
                text="27",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 27)),
            ),
            ButtonConfig(
                text="28",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 28)),
            ),
        ],
        [
            ButtonConfig(
                text="29",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 29)),
            ),
            ButtonConfig(
                text="30",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 30)),
            ),
            ButtonConfig(
                text="31",
                callback_data=DateCallbackData(entity="cal", action="go", id=1).with_date(dt.date(2024, 7, 31)),
            ),
            ButtonConfig(text=" ", callback_data=cb.EMPTY),
            ButtonConfig(text=" ", callback_data=cb.EMPTY),
            ButtonConfig(text=" ", callback_data=cb.EMPTY),
            ButtonConfig(text=" ", callback_data=cb.EMPTY),
        ],
        # Month navigation buttons
        [
            ButtonConfig(
                text=str(ButtonMessages.GO_BACK),
                callback_data=DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2024, 6, 16)),
            ),
            ButtonConfig(text=str(MonthList[6]), callback_data=cb.EMPTY),
            ButtonConfig(
                text=str(ButtonMessages.GO_FORWARD),
                callback_data=DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2024, 8, 16)),
            ),
        ],
        # Years navigation buttons
        [
            ButtonConfig(text=str(2024), callback_data=cb.EMPTY),
            ButtonConfig(
                text=str(ButtonMessages.GO_FORWARD),
                callback_data=DateCallbackData(entity="nav", action="go", id=1).with_date(dt.date(2025, 7, 16)),
            ),
        ],
    ]
