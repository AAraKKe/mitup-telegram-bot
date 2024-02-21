from enum import StrEnum
from string import Template
from typing import Any

from mitup_bot.utils import Emojis

CHARACTERS_TO_SCAPE = [
    ".",
    "!",
]


def _sanitize(message: str) -> str:
    for character in CHARACTERS_TO_SCAPE:
        message = message.replace(character, f"\\{character}")
    return message


class MessageBase(StrEnum):
    def get(self, **kwargs: Any) -> str:
        return _sanitize(Template(self.value).substitute(**kwargs))


class Messages(MessageBase):
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


class ButtonMessages(MessageBase):
    BUTTON_NEW_MEETING = f"{Emojis.NEW_MEETING} New meeting"
    BUTTON_ACTIVE_MEETINGS = f"{Emojis.LIST} Your active meetings"
    BUTTON_PAST_MEETINGS = f"{Emojis.PAST} Your past meetings"
    BUTTON_JOINED_MEETINGS = f"{Emojis.JOINED} Joined meetings"
    BUTTON_SETTINGS = f"{Emojis.SETTINGS} Settings"
    BUTTON_HELP = f"{Emojis.HELP} Help"
    BUTTON_COLLABORATE = f"{Emojis.HEART} Collaborate"
    BUTTON_LANGUAGE = f"{Emojis.LANG} Language"
    BUTTON_TIMEOUT = f"{Emojis.TIMEOUT} Timeout"
    BUTTON_NOTIFICATIONS = f"{Emojis.NOTIF} Notifications"
    BUTTON_TIMEZONE = f"{Emojis.TIME} Timezone"
    BUTTON_DEFAULT_OPTIONS = f"{Emojis.PEOPLE} Default Options"
    BUTTON_PRIVACY = f"{Emojis.SHIELD} Privacy"
    BUTTON_MAIN_MENU = "≪ Main Menu"
    BUTTON_CANCEL = f"{Emojis.CANCEL} Cancel"
