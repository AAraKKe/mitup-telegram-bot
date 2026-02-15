from dataclasses import dataclass
from enum import Enum, auto
from itertools import batched
from math import ceil
from typing import Self, assert_never

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
        assert len(str_value.encode()) <= 64, (
            f"The callback_data {str_value!r} is bigger than the 64B allowed by Telegram"
        )
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
    def markup(self) -> InlineKeyboardMarkup:
        return self.keyboard_to_markup(self.keyboard)

    def with_context(self, message: str) -> Self:
        """Add a message to the context of the view. This is useful to add information about the view"""
        self.description = f"{message}\n\n{self.description}"

        return self

    def with_footnote(self, text: str) -> Self:
        """Append a footnote at the end of the view's description."""
        self.description = f"{self.description}\n\n{text}"

        return self

    def with_context_menu(self, keyboard: Keyboard) -> Self:
        """Add a keyboard to be attached below the view keyboard. This can be used to add back buttons to views"""
        self.keyboard += keyboard

        return self

    def with_back_button(self, text: ButtonMessages, lang: str, callback_data: CallbackData) -> Self:
        """Add a back button to the view"""
        back_text = text.back(lang=lang)
        self.keyboard += [[ButtonConfig(text=back_text, callback_data=callback_data)]]

        return self

    @staticmethod
    def keyboard_to_markup(keyboard: Keyboard) -> InlineKeyboardMarkup:
        inline_keyboard = [[button_config.button for button_config in row] for row in keyboard]
        return InlineKeyboardMarkup(inline_keyboard)


@dataclass
class MitupInlineView(MitupView):
    """
    MitupView that represent an inline view with a title and an id.

    Intended to be used as the representation of a meeting when using inline queries.
    """

    title: str
    inline_description: str
    id: str


class PaginatedViewPosition(Enum):
    UNIQUE = auto()
    FIRST = auto()
    MIDDLE = auto()
    LAST = auto()


class PaginatedMitupView(MitupView):
    """
    A view that displays buttons in a paginated format.

    This class manages the pagination of buttons, allowing users to navigate through multiple pages of button
    configurations.

    Args:
        description (str): A description of the view.
        buttons (list[ButtonConfig]): A list of button configurations to display.
        page_number (int): The current page number to display. Starts at 1.
        navigation_callback_data (CallbackData): The callback data to use for navigation buttons.
                                                 Mandatory if there are more than one page.
        row_size (int, optional): The number of buttons per row. Defaults to 2.
        column_size (int, optional): The number of buttons per column. Defaults to 2.
    """

    def __init__(
        self,
        *,
        description: str,
        buttons: list[ButtonConfig],
        page_number: int,
        navigation_callback_data: cb.CallbackData | None = None,
        row_size: int = 2,
        column_size: int = 2,
    ):
        self.row_size = row_size
        self.column_size = column_size
        self.page_size = row_size * column_size
        self.page_number = page_number
        # Specify type, ty does nto deal well yet with generic protocols (implemented in ceil typing)
        self.total_pages: int = ceil(len(buttons) / self.page_size)
        self.buttons = buttons
        # Define type as any position so type checkers do not need to infer it
        self.position: PaginatedViewPosition

        if self.total_pages == 1:
            self.position = PaginatedViewPosition.UNIQUE
        elif self.page_number == 1:
            self.position = PaginatedViewPosition.FIRST
        elif self.page_number == self.total_pages:
            self.position = PaginatedViewPosition.LAST
        else:
            self.position = PaginatedViewPosition.MIDDLE

        keyboard = self.__get_paginated_view(navigation_callback_data)
        super().__init__(description, keyboard)

    def __get_paginated_view(self, navigation_callback_data: CallbackData | None) -> list[ButtonRow]:
        if self.page_number <= 0 or self.page_number > self.total_pages:
            raise ValueError("Invalid paginated position")

        first_button = (self.page_number - 1) * self.page_size
        last_button = min(len(self.buttons), first_button + self.page_size)

        button_in_page = self.buttons[first_button:last_button]
        keyboard = [list(row) for row in batched(button_in_page, self.column_size, strict=False)]
        if self.position is not PaginatedViewPosition.UNIQUE:
            if navigation_callback_data is None:
                raise ValueError("navigation_callback_data is required when there are more than one page")
            keyboard += [self.__match_navigation_button(navigation_callback_data)]

        return keyboard

    def __match_navigation_button(self, navigation_callback_data: CallbackData) -> ButtonRow:
        go_back = ButtonConfig(
            text=ButtonMessages.GO_BACK,
            callback_data=navigation_callback_data.with_id(self.page_number - 1),
        )
        go_forward = ButtonConfig(
            text=ButtonMessages.GO_FORWARD,
            callback_data=navigation_callback_data.with_id(self.page_number + 1),
        )

        match self.position:
            case PaginatedViewPosition.MIDDLE:
                return [go_back, go_forward]
            case PaginatedViewPosition.FIRST:
                return [go_forward]
            case PaginatedViewPosition.LAST:
                return [go_back]
            case PaginatedViewPosition.UNIQUE:
                return []
            case _ as unreachable:
                assert_never(unreachable)
