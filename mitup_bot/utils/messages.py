from enum import StrEnum
from string import Template

from mitup_bot.utils import Emojis

# https://core.telegram.org/bots/api#markdownv2-style
# Keep here only characters that we can use in normal text but not markdown
# Do not add any character we normally use to format markdown text.
CHARACTERS_TO_SCAPE = ["~", ">", "#", "+", "-", "=", "|", ".", "!"]
# Include all reserved characters to scape in user input
USER_INPUT_CHARACTERS_TO_SCAPE = CHARACTERS_TO_SCAPE + ["*", "_", "[", "]", "(", ")", "`", "{", "}"]


MessageParams = str | int | float | None


def _sanitize(message: str, full=False) -> str:
    to_scape = USER_INPUT_CHARACTERS_TO_SCAPE if full else CHARACTERS_TO_SCAPE
    for character in to_scape:
        message = message.replace(character, f"\\{character}")
    return message


class MessageBase(StrEnum):
    def get(self, full: bool = True, lang: str = "en", **kwargs: MessageParams) -> str:
        for key, value in kwargs.items():
            assert value is not None, "Message parameter cannot be None!"
            kwargs[key] = _sanitize(str(value), full=full)
        return Template(_sanitize(self.to_lang(lang))).substitute(**kwargs)

    def to_lang(self, lang: str) -> str:
        """Given a message, return the translation in the given language."""
        # For now we are not yet translating messages but here is where we should implement it
        return self.value


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
    SET_TIME = f"{Emojis.CLOCK} Set time"
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
    GO_BACK = "≪"
    GO_FORWARD = "≫"
    BACK_EDIT = f"{GO_BACK} Edit"
    MEETING_LOCATION_NAME = f"{Emojis.TITLE} Name"
    MEETING_LOCATION_COORDINATES = f"{Emojis.PIN} Location"
    MEETING_MAX_PARTICIPANTS = "Max participants"
    MEETING_NO_LIMIT_PARTICIPANTS = "No limit"
    MEETING_KICK_OUT = "Kick out"
    CONFIRM = f"{Emojis.CHECK} Confirm"
    DECLINE = f"{Emojis.CANCEL} Decline"
    DELETE_DATE = f"{Emojis.DELETE} Delete date"


class Messages(MessageBase):
    DEFAULT_MAIN_MENU_DESCRIPTION = "Welcome to Mitup Bot!\n" "Choose one of the following options:"
    DEFAULT_SETTINGS_DESCRIPTION = "Configure MitUp\\."


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
    # Meeting creation
    CREATE = "Lets create a meeting. What is the title?"
    CREATED_SUCCESS = (
        "A meeting has been created with the title: *$title*\n\n"
        "You can add more information to the meeting with the options below. "
        "The information which has not been added won't be shown when the meeting is shared.\n\n"
        f"When finished click on {Emojis.CHECK}"
    )

    # Meeting information
    FEATURES = (
        "*$title* \\(Created by: $owner\\)\n\n"
        f"--- {Emojis.DESCRIPTION} $description\n"
        f"--- {Emojis.CLOCK} $datetime\n"
        f"--- {Emojis.MAP} $location\n"
        f"--- {Emojis.JOINED} $participants\n"
    )
    DESCRIPTION_NOT_SET = f"{Emojis.PROHIB} No description defined {Emojis.PROHIB}"
    DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"
    LOCATION_NOT_SET = f"{Emojis.PROHIB} No location defined {Emojis.PROHIB}"
    EMPTY = "Empty"
    MAX_PARTICIPANTS = "(Max: $max_participants)"
    MEETING_WITHOUT_DESCRIPTION = "_This meeting has no description yet_"
    MEETING_WITHOUT_PARTICIPANTS = "_This meeting has no participants yet_"

    # Edit title and description
    EDIT_MEETING_TITLE = "This is the current title of your meeting:\n*$title*\n\n Send me the new one"
    EDIT_MEETING_DESCRIPTION = "This is the current description of your meeting:\n$description\n\n Send me the new one"
    TITLE_SET_SUCCESS = "The title has been properly set to: *$title*"
    DESCRIPTION_SET_SUCCESS = "The description has been properly set to: *$description*"

    # Edit meeting location
    EDIT_MEETING_LOCATION = (
        "A meeting can have a location associated. "
        "You can just set the name of the place or you can also attach the location. "
        "Choose any of the two options."
    )
    EDIT_MEETING_LOCATION_NAME = "Send me the name of the place."
    EDIT_MEETING_LOCATION_COORDINATES = (
        f"_Only from the phone {Emojis.PHONE}_\n\n"
        f"Send the location of the meeting. Touch on the {Emojis.CLIP} icon and then choose location. "
        "You can send whatever location you want, not just your current location."
    )
    LOCATION_NAME_SET_SUCCESS = "The name of the location has been set to: *$name*"
    LOCATION_COORDINATES_SUCCESS = "The location has been saved successfuly"
    LOCATION_COORDINATES_WRONG = "Send me the location again. Remember to touch on the clip icon and choose location."

    # Edit meeting participants information
    EDIT_MEETING_PARTICIPANTS = (
        "Here you will be able to manage the participants of the meeting: you can set the "
        "maximum number of people that can attend the meeting as well as kick out any of the "
        "participants that joined the meeting."
    )
    EDIT_MEETING_MAX_PARTICIPANTS = (
        "Send me the maximum number of members allowed in the meeting \\(must be a number greater than 0\\) "
        "or press in _No limit_ to allow an unlimited number of participants."
    )
    MAX_PARTICIPANTS_SET_SUCCESS = "The maximum number of participants has been set to: *$max_participants*"
    NO_LIMIT_PARTICIPANTS = "No limit"
    MAX_PARTICIPANTS_SET_FAIL = (
        "The maximum number of participants must be a number greater than 0. Please, try again\n"
    )
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = "These are the users that joined the meeting. Choose who you want to kick out."

    # Delete meeting
    DELETE_MEETING = "Are you sure you want to delete this meeting?"
    DELETE_MEETING_SUCCESS = "The meeting has been deleted successfully"
    DELETE_MEETING_DECLINE = "The meeting won't be deleted"
    ACCESS_TO_DELETED_MEETING = "This meeting has been deleted"

    # Edit meeting date and time
    EDIT_DATE = (
        f"Select the new date. Press **{ButtonMessages.DELETE_DATE}** if you want to "
        "unset the date and time of the meeting."
    )
    ADD_DATE = "Select the date."
    NEW_DATE_SET_SUCCESS = (
        f"The date has been set to: **$datetime**. To set the time press _{ButtonMessages.SET_TIME}_, "
        f"othwerise press _{ButtonMessages.BACK_EDIT}_ to go back to editing the meeting."
    )
    DATE_UPDATE_SUCCESS = "The date has been set to: **$datetime**"
    EDIT_TIME = "Send me the time of the meeting in the format _HH:MM_"
    EDIT_TIME_SUCCESS = "The time of the meeting has been set to **$datetime**"
    WRONG_TIME_FORMAT = (
        f"Not sure I understand that time {Emojis.THINK}...\n\n"
        "Please, send the time in the format _HH:MM_, for example _15:30_ or _09:15_"
    )
    INVALID_TIME = (
        f"My internal clock is old and cannot understand this science fiction time {Emojis.UFO}.\n\n"
        "Try again with a time that has valid hours \\(00-23\\) and minutes \\(00-59\\), "
        f"I am sure I can work with that {Emojis.BRAIN}."
    )
    DATE_TIME_DELETED = "The date and time of the meeting have been deleted successfully"

    # Show meeting list
    # TODO: this needs to be moved to a separate set of messages as it is not part of the meeting creation/editing
    # See https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/75
    NO_MEETINGS_FOUND = "_You don't have any meetings yet. Click on New meeting in the main menu to create one._"
    ACTIVE = "These are all your active meetings."


class Weekday(MessageBase):
    MONDAY = "Mon"
    TUESDAY = "Tue"
    WEDNESDAY = "Wed"
    THURSDAY = "Thu"
    FRIDAY = "Fri"
    SATURDAY = "Sat"
    SUNDAY = "Sun"


class Month(MessageBase):
    JANUARY = "January"
    FEBRUARY = "February"
    MARCH = "March"
    APRIL = "April"
    MAY = "May"
    JUNE = "June"
    JULY = "July"
    AUGUST = "August"
    SEPTEMBER = "September"
    OCTOBER = "October"
    NOVEMBER = "November"
    DECEMBER = "December"


class MonthShort(MessageBase):
    JANUARY = "Jan"
    FEBRUARY = "Feb"
    MARCH = "Mar"
    APRIL = "Apr"
    MAY = "May"
    JUNE = "Jun"
    JULY = "Jul"
    AUGUST = "Aug"
    SEPTEMBER = "Sep"
    OCTOBER = "Oct"
    NOVEMBER = "Nov"
    DECEMBER = "Dec"


MonthList = [
    Month.JANUARY,
    Month.FEBRUARY,
    Month.MARCH,
    Month.APRIL,
    Month.MAY,
    Month.JUNE,
    Month.JULY,
    Month.AUGUST,
    Month.SEPTEMBER,
    Month.OCTOBER,
    Month.NOVEMBER,
    Month.DECEMBER,
]
MonthShortList = [
    MonthShort.JANUARY,
    MonthShort.FEBRUARY,
    MonthShort.MARCH,
    MonthShort.APRIL,
    MonthShort.MAY,
    MonthShort.JUNE,
    MonthShort.JULY,
    MonthShort.AUGUST,
    MonthShort.SEPTEMBER,
    MonthShort.OCTOBER,
    MonthShort.NOVEMBER,
    MonthShort.DECEMBER,
]
