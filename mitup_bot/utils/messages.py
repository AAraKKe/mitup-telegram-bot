from enum import StrEnum
from string import Template
from typing import Any

from mitup_bot.utils import Emojis

CHARACTERS_TO_SCAPE = [
    ".",
    "!",
    "(",
    ")",
    "-",
    "<",
    ">",
    "&",
    "|",
    "_",
    "{",
    "}",
    "[",
    "]",
    "~",
    "`",
    "#",
    "+",
]


def _sanitize(message: str) -> str:
    for character in CHARACTERS_TO_SCAPE:
        message = message.replace(character, f"\\{character}")
    return message


class MessageBase(StrEnum):
    def get(self, **kwargs: Any) -> str:
        return _sanitize(Template(self.value).substitute(**kwargs))


class Messages(MessageBase):
    DEFAULT_MAIN_MENU_DESCRIPTION = "Welcome to Mitup Bot! \n" "Choose one of the following options:"
    DEFAULT_SETTINGS_DESCRIPTION = "Configure MitUp."


class SettingsMessages(MessageBase):
    SET_TIMEZONE_SETTINGS = (
        "Your timezone is set to *$timezone*. \n"
        "Send me the name of your city or your location to set your "
        "timezone or touch in *Cancel* to go back."
    )
    TIMEZONE_SETTINGS_SET_SUCCESS = "Your timezone has been set to: *$timezone* "
    SET_REGISTRATION_TIMEZONE = "Welcome to Mitup Bot $first_name! Please, tell me your timezone."
    REGISTRATION_TIMEZONE_SET_SUCCESS = "Perfect! Your timezone is $timezone"
    REGISTRATION_TIMEZONE_SET_FAIL = "I'm sorry, I couldn't set your timezone. Please, try again."


class MeetingMessages(MessageBase):
    CREATE = "Lets create a meeting. What is the title?"
    CREATED_SUCCESS = (
        "A meeting has been created with the title: *$title*\n\n"
        "You can add more information to the meeting with the options below. "
        "The information which has not been added won't be shown when the meeting is shared.\n\n"
        f"When finished click on {Emojis.CHECK}"
    )
    FEATURES = (
        "*$title* (Created by: $owner)\n\n"
        f"--- {Emojis.DESCRIPTION} $description\n"
        f"--- {Emojis.CLOCK} $date\n"
        f"--- {Emojis.MAP} $location\n"
        f"--- {Emojis.JOINED} $participants\n"
    )
    DESCRIPTION_NOT_SET = f"{Emojis.PROHIB} No description defined {Emojis.PROHIB}"
    DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"
    LOCATION_NOT_SET = f"{Emojis.PROHIB} No location defined {Emojis.PROHIB}"
    PARTICIPANTS_NOT_SET = "Empty"


class MeetingMessages(MessageBase):
    CREATE_MEETING_TITLE = "Lets create a meeting. What is the title?"

    MEETING_CREATED_SUCCESS = (
        "A meeting has been created with the title: *$title*\n\n"
        "You can add more information to the meeting with the options below. "
        "The information which has not been added won't be shown when the meeting is shared.\n\n"
        f"When finished click on {Emojis.CHECK}"
    )

    MEETING_FEATURES = (
        "*$title* (Created by: $owner)\n\n"
        f"--- {Emojis.DESCRIPTION} $description\n"
        f"--- {Emojis.CLOCK} $date\n"
        f"--- {Emojis.MAP} $location\n"
        f"--- {Emojis.JOINED} $participants\n"
    )

    MEETING_DESCRIPTION_NOT_SET = f"{Emojis.PROHIB} No description defined {Emojis.PROHIB}"

    MEETING_DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"

    MEETING_LOCATION_NOT_SET = f"{Emojis.PROHIB} No location defined {Emojis.PROHIB}"

    MEETING_PARTICIPANTS_NOT_SET = "Empty"


class ButtonMessages(MessageBase):
    NEW_MEETING = f"{Emojis.NEW_MEETING} New meeting"
    ACTIVE_MEETINGS = f"{Emojis.LIST} Your active meetings"
    PAST_MEETINGS = f"{Emojis.PAST} Your past meetings"
    JOINED_MEETINGS = f"{Emojis.JOINED} Joined meetings"
    SETTINGS = f"{Emojis.SETTINGS} Settings"
    HELP = f"{Emojis.HELP} Help"
    COLLABORATE = f"{Emojis.HEART} Collaborate"
    LANGUAGE = f"{Emojis.LANG} Language"
    TIMEOUT = f"{Emojis.TIMEOUT} Timeout"
    NOTIFICATIONS = f"{Emojis.NOTIF} Notifications"
    TIMEZONE = f"{Emojis.TIME} Timezone"
    DEFAULT_OPTIONS = f"{Emojis.PEOPLE} Default Options"
    PRIVACY = f"{Emojis.SHIELD} Privacy"
    MAIN_MENU = "≪ Main Menu"
    CANCEL = f"{Emojis.CANCEL} Cancel"
    TITLE = f"{Emojis.TITLE} Title"
    DESCRIPTION = f"{Emojis.DESCRIPTION} Description"
    DATE = f"{Emojis.CALENDAR} Date"
    CLOCK = f"{Emojis.CLOCK} Time"
    PARTICIPANTS = f"{Emojis.JOINED} Participants"
    LOCATION = f"{Emojis.MAP} Location"
    DONE = f"{Emojis.CHECK} Done"
    JOIN = f"{Emojis.CHECK} Join"
    INVITE = f"{Emojis.FRIEND} Invite"
    LEAVE = f"{Emojis.CANCEL} Leave"
    DELETE = f"{Emojis.DELETE} Delete"
    EDIT = f"{Emojis.EDIT} Edit"
    SHARE = f"{Emojis.SHARE} Share"
    CHAT = f"{Emojis.CHAT} Chat"
