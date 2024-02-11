from collections.abc import Sequence
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class ButtonConfig:
    text: str
    callback_data: str | object

    @property
    def button(self) -> InlineKeyboardButton:
        kwargs = {
            "callback_data": self.callback_data,
        }
        return InlineKeyboardButton(self.text, **kwargs)  # type: ignore


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
