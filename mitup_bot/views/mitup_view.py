from dataclasses import dataclass
from enum import Enum, auto
from itertools import batched
from math import ceil
from typing import Self

from pydantic import BaseModel, field_validator
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from mitup_bot.callback_data import CallbackData
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb


class ButtonConfig(BaseModel):
    text: str
    # Allow str as type as an intermediate step for backward compatibility
    # will be moving this to CallbackData to make sure we have safeguards in
    # the future
    callback_data: CallbackData | str | None = None
    switch_inline_query: str | None = None

    @field_validator("callback_data")
    @classmethod
    def validate_callback_data(cls, value: CallbackData | str | None) -> CallbackData | str | None:
        str_value = str(value)
        assert (
            len(str_value.encode()) <= 64
        ), f"The callback_data {str_value!r} is bigger than the 64B allowed by Telegram"
        return value

    @property
    def button(self) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self.text,
            callback_data=str(self.callback_data) if self.callback_data else None,
            switch_inline_query=self.switch_inline_query,
        )


ButtonRow = list[ButtonConfig]
Keyboard = list[ButtonRow]


@dataclass
class MitupView:
    description: str
    keyboard: Keyboard

    @property
    def markup(self):
        return self.keyboard_to_markup(self.keyboard)

    def with_context(self, message: str) -> Self:
        self.description = f"{message}\n\n{self.description}"

        return self

    @staticmethod
    def keyboard_to_markup(keyboard: Keyboard) -> InlineKeyboardMarkup:
        inline_keyboard = [[button_config.button for button_config in row] for row in keyboard]
        return InlineKeyboardMarkup(inline_keyboard)


@dataclass
class MitupInlineView(MitupView):
    """MitupView that represent an inline view with a title and an id."""

    title: str
    inline_description: str
    id: str


class PaginatedViewPosition(Enum):
    UNIQUE = auto()
    FIRST = auto()
    MIDDLE = auto()
    LAST = auto()


class PaginatedMitupView(MitupView):
    def __init__(
        self,
        *,
        description: str,
        buttons: list[ButtonConfig],
        page_number: int,
        row_size: int = 2,
        column_size: int = 2,
    ):
        self.row_size: int = row_size
        self.column_size: int = column_size
        self.page_size: int = row_size * column_size
        self.page_number: int = page_number
        self.total_pages: int = ceil(len(buttons) / self.page_size)
        self.buttons: list[ButtonConfig] = buttons

        if self.total_pages == 1:
            self.position = PaginatedViewPosition.UNIQUE
        elif self.page_number == 1:
            self.position = PaginatedViewPosition.FIRST
        elif self.page_number == self.total_pages:
            self.position = PaginatedViewPosition.LAST
        else:
            self.position = PaginatedViewPosition.MIDDLE

        keyboard = self.__get_paginated_view()
        super().__init__(description, keyboard)

    def __get_paginated_view(self) -> list[ButtonRow]:
        if self.page_number <= 0 or self.page_number > self.total_pages:
            raise ValueError("Invalid paginated position")

        first_button = (self.page_number - 1) * self.page_size
        last_button = len(self.buttons) if self.page_number == self.total_pages else first_button + self.page_size

        button_in_page = self.buttons[first_button:last_button]
        # keyboard = self.__match_action_buttons(button_in_page)
        keyboard = [list(row) for row in batched(button_in_page, self.row_size)]
        keyboard += self.__match_navigation_button()

        return keyboard

    def __match_navigation_button(self) -> list[ButtonRow]:
        keyboard: list[ButtonRow] = []
        navegation_button_row: list[ButtonConfig] = []

        if self.position in {PaginatedViewPosition.MIDDLE, PaginatedViewPosition.LAST}:
            navegation_button_row.append(
                ButtonConfig(
                    text=ButtonMessages.GO_BACK, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(self.page_number - 1)
                )
            )
        if self.position in {PaginatedViewPosition.MIDDLE, PaginatedViewPosition.FIRST}:
            navegation_button_row.append(
                ButtonConfig(
                    text=ButtonMessages.GO_FORWARD,
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(self.page_number + 1),
                )
            )

        if navegation_button_row:
            keyboard.append(navegation_button_row)
        keyboard.append([ButtonConfig(text=ButtonMessages.MAIN_MENU, callback_data=cb.MAIN_MENU)])

        return keyboard
