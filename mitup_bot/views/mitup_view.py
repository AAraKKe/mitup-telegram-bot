from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, field_validator
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from mitup_bot.callback_data import CallbackData


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
            self.text,
            callback_data=str(self.callback_data) if self.callback_data else None,
            switch_inline_query=self.switch_inline_query,
        )


ButtonRow = Sequence[ButtonConfig]
Keyboard = Sequence[ButtonRow]


@dataclass
class MitupView:
    description: str
    keyboard: Keyboard

    @property
    def markup(self):
        inline_keyboard = [[button_config.button for button_config in row] for row in self.keyboard]
        return InlineKeyboardMarkup(inline_keyboard)

    def with_context(self, message: str) -> Self:
        self.description = f"{message}\n\n{self.description}"

        return self
