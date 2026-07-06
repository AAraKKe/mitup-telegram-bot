from dataclasses import dataclass
from enum import Enum, auto
from itertools import batched
from math import ceil
from typing import Self, assert_never

from pydantic import BaseModel, field_serializer, field_validator, model_validator
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from mitup_bot.callback_data import CallbackData
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText


class ButtonConfig(BaseModel):
    text: str | FormattedText
    callback_data: CallbackData | str | None = None
    switch_inline_query: str | None = None
    switch_inline_query_current_chat: str | None = None
    # An https:// or tg:// deep link. Used by buttons that open an external page (e.g. the Patreon
    # OAuth consent screen), which produce no callback query and so carry no callback_data.
    url: str | None = None

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @field_validator("callback_data")
    @classmethod
    def validate_callback_data(cls, value: CallbackData | str | None) -> CallbackData | str | None:
        str_value = str(value)
        if len(str_value.encode("utf-8")) > 64:
            raise ValueError(f"The callback_data {str_value!r} is bigger than the 64B allowed by Telegram")
        return value

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str | FormattedText) -> str | FormattedText:
        if isinstance(value, FormattedText):
            if value.entities:
                raise ValueError("ButtonConfig text should not contain entities")
            return value
        return value

    @model_validator(mode="after")
    def validate_exactly_one_action(self) -> Self:
        action_count = sum(
            f is not None
            for f in [
                self.callback_data,
                self.switch_inline_query,
                self.switch_inline_query_current_chat,
                self.url,
            ]
        )
        if action_count != 1:
            raise ValueError(
                "Exactly one of callback_data, switch_inline_query, switch_inline_query_current_chat, "
                "or url must be set"
            )
        return self

    @field_serializer("text")
    def serialize_text(self, value: str | FormattedText) -> str:
        return value if isinstance(value, str) else value.text

    @property
    def button(self) -> InlineKeyboardButton:
        text = self.text if isinstance(self.text, str) else self.text.text
        return InlineKeyboardButton(
            text=text,
            callback_data=str(self.callback_data) if self.callback_data else None,
            switch_inline_query=self.switch_inline_query,
            switch_inline_query_current_chat=self.switch_inline_query_current_chat,
            url=self.url,
        )


ButtonRow = list[ButtonConfig]
Keyboard = list[ButtonRow]


class MitupView:
    def __init__(self, description: str | FormattedText, keyboard: Keyboard):
        self.description: FormattedText = (
            description if isinstance(description, FormattedText) else FormattedText(description)
        )
        self.keyboard = keyboard

    @property
    def markup(self) -> InlineKeyboardMarkup | None:
        return self.keyboard_to_markup(self.keyboard) if self.keyboard else None

    def with_context(self, message: str | FormattedText) -> Self:
        """Prepend *message* to the description, adjusting entity offsets when needed."""
        ftext = message if isinstance(message, FormattedText) else FormattedText(message)
        self.description = self.description.prepend(ftext.append("\n\n"))
        return self

    def with_footnote(self, text: str | FormattedText) -> Self:
        """Append a footnote at the end of the view's description, preserving its entities."""
        self.description = self.description.append("\n\n").append(text)
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self.description == other.description and self.keyboard == other.keyboard

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(description={self.description!r}, keyboard={self.keyboard!r})"

    @staticmethod
    def keyboard_to_markup(keyboard: Keyboard) -> InlineKeyboardMarkup:
        inline_keyboard = [[button_config.button for button_config in row] for row in keyboard]
        return InlineKeyboardMarkup(inline_keyboard)


class MitupInlineView(MitupView):
    """
    MitupView that represent an inline view with a title and an id.

    Intended to be used as the representation of a meeting when using inline queries.
    """

    def __init__(
        self,
        *,
        description: str | FormattedText,
        keyboard: Keyboard,
        title: str | FormattedText,
        inline_description: str | FormattedText,
        id: str,
    ):
        super().__init__(description, keyboard)
        self.title = title if isinstance(title, str) else title.text
        self.inline_description = inline_description if isinstance(inline_description, str) else inline_description.text
        self.id = id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MitupInlineView):
            return NotImplemented
        return (
            self.description == other.description
            and self.keyboard == other.keyboard
            and self.title == other.title
            and self.inline_description == other.inline_description
            and self.id == other.id
        )


@dataclass
class InlineResultsButton:
    """Button shown above inline query results.

    Abstracts the Telegram `InlineQueryResultsButton` so handlers never
    depend on the PTB type directly.
    """

    text: str
    start_parameter: str | None = None


class PaginatedViewPosition(Enum):
    UNIQUE = auto()
    FIRST = auto()
    MIDDLE = auto()
    LAST = auto()


def arrange_in_grid(buttons: list[ButtonConfig], column_size: int) -> list[ButtonRow]:
    return [list(row) for row in batched(buttons, column_size, strict=False)]


class GridMitupView(MitupView):
    """
    A non-paginated view that arranges a flat button list into a grid.

    Use this instead of `PaginatedMitupView` when all buttons fit on a single
    screen and pagination is never needed — e.g. a fixed-size language picker.
    The number of rows grows with the button count; only the column count is
    specified by the caller.
    """

    def __init__(
        self,
        *,
        description: str | FormattedText,
        buttons: list[ButtonConfig],
        column_size: int = 2,
    ):
        super().__init__(description, arrange_in_grid(buttons, column_size))


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
        row_size (int, optional): The number of buttons per row. Defaults to DEFAULT_ROW_SIZE.
        column_size (int, optional): The number of buttons per column. Defaults to DEFAULT_COLUMN_SIZE.
    """

    DEFAULT_ROW_SIZE = 2
    DEFAULT_COLUMN_SIZE = 2
    DEFAULT_PAGE_SIZE = DEFAULT_ROW_SIZE * DEFAULT_COLUMN_SIZE

    @classmethod
    def clamp_page(cls, page_number: int, item_count: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
        """Clamp a requested page into the valid range for `item_count` items.

        Callback data can carry a stale page (e.g. the last item of the last page was deleted, or
        the list shrank since the button was rendered), so handlers clamp the requested page
        before building the view instead of failing on an out-of-range position.
        """
        total_pages = max(1, ceil(item_count / page_size))
        return max(1, min(page_number, total_pages))

    def __init__(
        self,
        *,
        description: str | FormattedText,
        buttons: list[ButtonConfig],
        page_number: int,
        navigation_callback_data: cb.CallbackData | None = None,
        row_size: int = DEFAULT_ROW_SIZE,
        column_size: int = DEFAULT_COLUMN_SIZE,
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
        keyboard = arrange_in_grid(button_in_page, self.column_size)
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
