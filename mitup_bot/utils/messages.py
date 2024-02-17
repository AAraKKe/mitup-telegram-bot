from enum import Enum
from string import Template
from typing import Any

CHARACTERS_TO_SCAPE = [
    ".",
    "!",
]


def _sanitize(message: str) -> str:
    for character in CHARACTERS_TO_SCAPE:
        message = message.replace(character, f"\\{character}")
    return message


class Messages(Enum):
    SET_TIMEZONE_SETTINGS = (
        "Your timezone is set to *$timezone*. \n"
        "Send me the name of your city or your location to set your "
        "timezone or touch in *Cancel* to go back."
    )

    TIMEZONE_SETTINGS_SET_SUCCESS = "Your timezone has been set to: *$timezone* "

    SET_REGISTRATION_TIMEZONE = "Welcome to Mitup Bot $first_name! Please, tell me your timezone."

    REGISTRATION_TIMEZONE_SET_SUCCESS = "Perfect! Your timezone is $timezone"

    DEFAULT_MAIN_MENU_DESCRIPTION = "Welcome to Mitup Bot! \n" "Choose one of the following options:"

    DEFAULT_SETTINGS_DESCRIPTION = "Configure MitUp."

    def get(self, **kwargs: Any) -> str:
        return _sanitize(Template(self.value).substitute(**kwargs))
