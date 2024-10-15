import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Literal, assert_never

from mitup_bot.callback_data import CallbackData, DateCallbackData
from mitup_bot.utils import ButtonMessages, Emojis, MonthList, Weekday
from mitup_bot.utils import callbacks as cb

from .mitup_view import ButtonConfig, ButtonRow, Keyboard


@dataclass
class CalendarKeyboard:
    """
    A Telegram inline keyboard that represents a calendar with navigation buttons.

    Two dates are provided to the calendar:
    - The anchor_date is the date we are defining as a the current real time date. This is used to be marked
    in the calendar with a green check emoji.
    - the current_date is the date the calendar is going to be shown for. This allows to navigate on the calendar
    to different months and years while making sure the calendar is still anchored in the anchor_date.

    Providing these two dates makes the calendar timezone agnostic and does not need to deal with whenter it is
    after midnight or not. It is up to whoever creates the calendar to make sure timezone is considered when providing
    the dates.

    The callback_data (of type DateCallbackData) is used to create the callback_data of each day button in the calendar.

    The navigation_callback_data (of type DateCallbackData) is used to create the callback_data of the navigation
    buttons (i.e. moving to the next/previous month or year).
    """

    anchor_date: dt.date
    current_date: dt.date
    callback_data: DateCallbackData
    navigation_callback_data: DateCallbackData
    lang: str = "en"

    @property
    def keyboard(self) -> Keyboard:
        """Generates the keyboard with the calendar and navigation buttons."""

        keyboard = [self.__weekdays_row()]
        keyboard += self.__month_rows()
        keyboard.append(self.__navigation_buttons(by="month"))
        keyboard.append(self.__navigation_buttons(by="year"))
        return keyboard

    def __weekdays_row(self) -> ButtonRow:
        """Generates the row with the weekdays in the calendar."""
        return [
            ButtonConfig(text=Weekday.MONDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.TUESDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.WEDNESDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.THURSDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.FRIDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.SATURDAY.get(lang=self.lang), callback_data=cb.EMPTY),
            ButtonConfig(text=Weekday.SUNDAY.get(lang=self.lang), callback_data=cb.EMPTY),
        ]

    def __month_rows(self) -> Keyboard:
        """Generates the rows of the calendar with the days of the month."""
        month_calendar = calendar.Calendar()

        def generate_text(day: dt.date) -> str:
            if day == self.anchor_date:
                return Emojis.CHECK.value
            return " " if day.month != self.current_date.month else str(day.day)

        def generate_cb(day: dt.date) -> CallbackData:
            return cb.EMPTY if day.month != self.current_date.month else self.callback_data.with_date(day)

        return [
            [ButtonConfig(text=generate_text(day), callback_data=generate_cb(day)) for day in week]
            for week in month_calendar.monthdatescalendar(self.current_date.year, self.current_date.month)
        ]

    def __navigation_back_buttons(self, by: Literal["month", "year"]) -> ButtonConfig:
        month = self.current_date.month
        year = self.current_date.year

        match (month, year, by):
            case (1, _, "month"):
                # If we are in January, we go back to December of the previous year
                month = 12
                year -= 1
            case (_, _, "month"):
                month -= 1
            case (_, _, "year"):
                year -= 1
            case (_, _, _) as unreachable:
                assert_never(unreachable)

        date_back = dt.date(year, month, self.anchor_date.day)
        return ButtonConfig(
            text=str(ButtonMessages.GO_BACK.get()),
            callback_data=self.navigation_callback_data.with_date(date_back),
        )

    def __navigation_forward_buttons(self, by: Literal["month", "year"]) -> ButtonConfig:
        month = self.current_date.month
        year = self.current_date.year

        match (month, year, by):
            case (12, _, "month"):
                # If we are in December, we go forward to January of the next year
                month = 1
                year += 1
            case (_, _, "year"):
                year += 1
            case (_, _, "month"):
                month += 1
            case (_, _, _) as unreachable:
                assert_never(unreachable)

        date_forward = dt.date(year, month, self.anchor_date.day)
        return ButtonConfig(
            text=str(ButtonMessages.GO_FORWARD.get()),
            callback_data=self.navigation_callback_data.with_date(date_forward),
        )

    def __navigation_buttons(self, by: Literal["month", "year"]) -> ButtonRow:
        """Generates the row with the navigation buttons to move delta days."""
        row = []

        # For the back button, the rule is:
        # - Add month back button if the current month/year are greater than the real, today month/year
        # - Only add year back button if the current year is greater than the real, today year
        add_back_button = self.current_date.year > dt.date.today().year or (
            self.current_date.month > dt.date.today().month and by == "month"
        )

        if add_back_button:
            row.append(self.__navigation_back_buttons(by))

        # We can always go forward. We add the current label and the navigation forward buttons.
        current_label = (
            self.current_date.year if by == "year" else MonthList[self.current_date.month - 1].get(lang=self.lang)
        )
        row.extend(
            (
                ButtonConfig(
                    text=str(current_label),
                    callback_data=cb.EMPTY,
                ),
                self.__navigation_forward_buttons(by),
            )
        )
        return row
