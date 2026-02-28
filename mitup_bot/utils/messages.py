from enum import StrEnum
from string import Template
from typing import Protocol

from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import Emojis

# https://core.telegram.org/bots/api#markdownv2-style
# Keep here only characters that we can use in normal text but not markdown
# Do not add any character we normally use to format markdown text.
CHARACTERS_TO_SCAPE = ["~", ">", "#", "+", "-", "=", "|", ".", "!"]
# Include all reserved characters to scape in user input
USER_INPUT_CHARACTERS_TO_SCAPE = CHARACTERS_TO_SCAPE + ["*", "_", "[", "]", "(", ")", "`", "{", "}"]


MessageParams = str | int | float | None


class TranslationEngineProtocol(Protocol):
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str: ...


def sanitize(message: str, full=False) -> str:
    to_scape = USER_INPUT_CHARACTERS_TO_SCAPE if full else CHARACTERS_TO_SCAPE
    for character in to_scape:
        message = message.replace(character, f"\\{character}")
    # Clean up double scapes if any
    return message.replace("\\\\", "\\")


class MessageBase(StrEnum):
    def get(
        self,
        *,
        lang: str = TranslationEngine.FALLBACK_LANG,
        full: bool = True,
        plain: bool = False,
        **kwargs: MessageParams,
    ) -> str:
        """
        Retrieves a formatted message string based on the provided parameters.
        Args:
            full (bool): If True, performs full sanitization on the message parameters. Defaults to True.
            lang (str): The language code to use for the message translation.
                Defaults to TranslationEngine.FALLBACK_LANG.
            plain (bool): If True, returns the message without sanitization. Defaults to False.
            **kwargs (MessageParams): Additional message parameters to be substituted into the message template.
        Returns:
            str: The formatted message string.
        """

        for key, value in kwargs.items():
            assert value is not None, f"Message parameter cannot be None and found {key}={value}!"
            kwargs[key] = str(value) if plain else sanitize(str(value), full=full)
        return Template(self.to_lang(lang) if plain else sanitize(self.to_lang(lang))).substitute(**kwargs)

    def to_lang(self, lang: str) -> str:
        """Given a message, return the translation in the given language."""
        # For now we are not yet translating messages but here is where we should implement it
        return self.translations_class().translate(self.id(), lang)

    def id(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"

    def translations_class(self) -> type[TranslationEngineProtocol]:
        """This allows defining specific MessageBase types for testing without needed to provide translations"""
        return TranslationEngine


class ButtonMessages(MessageBase):
    # Navigation buttons
    MAIN_MENU = "Main Menu"
    GO_BACK = "≪"
    GO_FORWARD = "≫"
    CONFIRM = f"{Emojis.CHECK} Confirm"
    DECLINE = f"{Emojis.CANCEL} Decline"

    # Main Menu buttons
    NEW_MEETING = f"{Emojis.NEW_MEETING} New meeting"
    ACTIVE_MEETINGS = f"{Emojis.LIST} Your active meetings"
    PAST_MEETINGS = f"{Emojis.PAST} Your past meetings"
    JOINED_MEETINGS = f"{Emojis.JOINED} Joined meetings"
    SETTINGS = f"{Emojis.SETTINGS} Settings"
    HELP = f"{Emojis.HELP} Help"
    COLLABORATE = f"{Emojis.HEART} Collaborate"

    # Settings buttons
    LANGUAGE = f"{Emojis.LANG} Language"
    TIMEOUT = f"{Emojis.HOURGLASS} Timeout"
    NOTIFICATIONS = f"{Emojis.NOTIF} Notifications"
    TIMEZONE = f"{Emojis.TIME} Timezone"
    DEFAULT_OPTIONS = f"{Emojis.PEOPLE} Default Options"
    PRIVACY = f"{Emojis.SHIELD} Privacy"
    WAITING_LIST = "Waiting list"
    PUBLIC = "Public"
    OPEN_INVITATION = "Open invitations"
    INCOGNITO = "Incognito"
    SHOW_TIMEZONE = "Show timezone"
    SHOW_IN_YOUR_TIMEZONE = "Show timezone"
    ENABLE = "Enable"
    DISABLE = "Disable"

    # Meeting buttons
    CANCEL = f"{Emojis.CANCEL} Cancel"
    TITLE = f"{Emojis.TITLE} Title"
    DESCRIPTION = f"{Emojis.DESCRIPTION} Description"
    DATE = f"{Emojis.CALENDAR} Date"
    TIME = f"{Emojis.CLOCK} Time"
    NOTIFICATIONS_TIME = f"{Emojis.NOTIF} Time"
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
    MEETING_LOCATION_NAME = f"{Emojis.TITLE} Name"
    MEETING_LOCATION_COORDINATES = f"{Emojis.PIN} Location"
    MEETING_MAX_PARTICIPANTS = "Max participants"
    MEETING_NO_LIMIT_PARTICIPANTS = "No limit"
    MEETING_KICK_OUT = "Kick out"
    DELETE_DATE = f"{Emojis.DELETE} Delete date"
    MAKE_SEARCHABLE = "Make it searchable"
    LOAD_CHAT_MEETINGS = f"{Emojis.SEARCH} Load meetings"
    SEARCH_CHAT_MEETINGS = f"{Emojis.SEARCH} Search meetings"

    # Notification buttons
    REACTIVATE_MEETING = "Reactivate meeting"

    def back(self, lang: str, full=True, **kwargs: str) -> str:
        return f"{self.GO_BACK} {self.get(lang=lang, full=full, plain=False, **kwargs)}"


class Messages(MessageBase):
    DEFAULT_MAIN_MENU_DESCRIPTION = "Welcome to Mitup Bot!\n\nChoose one of the following options:"
    DEFAULT_SETTINGS_DESCRIPTION = "Configure MitUp."


class SettingsMessages(MessageBase):
    # Timezone settings
    SET_TIMEZONE_SETTINGS = (
        "Your timezone is set to *${timezone}*. \n"
        "Send me the name of your city or your location to set your "
        "timezone or touch in *Cancel* to go back."
    )
    TIMEZONE_SETTINGS_SET_SUCCESS = "Your timezone has been set to: *${timezone}* "
    SET_REGISTRATION_TIMEZONE = (
        "Welcome to Mitup Bot ${first_name}!\n\n"
        "Let's start by setting your timezone. Send me the name of your city or, "
        f"for a more accurate result, your location by pressing on {Emojis.CLIP} and "
        "selecting Location.\n\n"
        "*Important*: we do not store your location and this information is only used to "
        "configure your timezone."
    )
    REGISTRATION_TIMEZONE_SET_SUCCESS = "Perfect! Your timezone is ${timezone}"
    REGISTRATION_TIMEZONE_SET_FAIL = "I'm sorry, I couldn't set your timezone. Please, try again."

    # Language settings
    SELECT_LANGUAGE = "Current language: *${language}*.\n\nSelect a language."
    LANGUAGE_SET_SUCCESS = "The language has been set successfully."

    # Default meeting defualt options
    DEFAULT_MEETING_OPTIONS_MESSAGE = (
        "Here you can configure the default options used when creating a meeting. "
        "Do you usually create public meetings? Set it here. "
        "Are you always allowing people to invite other people or to share the meeting in other chats? Do it here. "
        "All your meetings will inherit this configuration.\n\n"
        "You can configure different aspects of your meeting:\n\n"
        "*Waiting list*: allow users to join the meeting even when it is full. "
        "Users joining when it is full will be added to a waiting list and added to the participants "
        "list as soon as a spot is available in the order they joined.\n\n"
        "*Public*: activate this to allow everyone that receives the meeting to share it again. "
        "Perfect to reach more people.\n\n"
        "*Open invitations*: activate this option to allow users who have joined the meeting to add friends "
        "even if those friends are not in Telegram.\n\n"
        "*Incognito*: a meeting with incognito enabled won't show the people that joined the meeting when shared. "
        "Only the number of participants will be shown. You will still be able to see the participants.\n\n"
        "*Show timezone*: meetings usually include the timezone the date and time refers of the meeting to. "
        "If you don't need this information displayed on the meeting message you can disable it here. "
        "This will also disable the _Timezone_ button shown when the meeting is shared."
    )

    # Timeout messages
    SET_TIMEOUT_SETTINGS = (
        "Timeout defines how long a meeting is kept after it is over. After this time the meeting will be deactivated. "
        "You can activate it again from your *Past meetings menu*.\n\n"
        "The curent timeout is *${timeout} minutes*\n\n"
        "Send the timeout \\(in minutes\\) you would like to use or touch Cancel to go back."
    )
    INVALID_POSITIVE_INTEGER = (
        "Oops! That doesn't look like a valid number. Please enter a positive whole number. No decimals allowed!"
    )
    TIMEOUT_SET_SUCCESS = "The timeout has been set to: *${timeout} minutes*"
    NOTIFICATIONS_SETTINGS = (
        "Configure MeetUp notifications.\n\n"
        "Notifications: ${notifications_status}\n"
        "When notifications are enabled, MeetUp will notify "
        "you *${notifications_time} minutes* before a meeting starts."
    )
    NOTIFICATION_SET_TIME = (
        "Send how long before a meeting starts \\(in minutes\\) you would like to be "
        "notified or touch Cancel to go back."
    )
    NOTIFICATION_TIME_SET_SUCCESS = "The notification time has been set to *${notifications_time} minutes*."
    ENABLED = f"Enabled {Emojis.CHECK}"
    DISABLED = f"Disabled {Emojis.CANCEL}"


class MeetingMessages(MessageBase):
    # Meeting creation
    CREATE = "Lets create a meeting. What is the title?"
    CREATED_SUCCESS = (
        "A meeting has been created with the title: *${title}*\n\n"
        "You can add more information to the meeting with the options below. "
        "The information which has not been added won't be shown when the meeting is shared.\n\n"
        f"When finished click on {Emojis.CHECK} Done"
    )
    INVALID_TITLE = (
        f"I did not recognize what you sent as a valid title {Emojis.THINK}.\n\n"
        "Send a message with the title of the meeting or click on *Cancel*."
    )

    # Meeting information
    CREATED_BY = "Created by: ${owner}"
    DESCRIPTION_NOT_SET = f"{Emojis.PROHIB} No description defined {Emojis.PROHIB}"
    DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"
    LOCATION_NOT_SET = f"{Emojis.PROHIB} No location defined {Emojis.PROHIB}"
    EMPTY = "Empty"
    PARTICIPANT = "Participant"
    PARTICIPANTS = "Participants"
    MAX_PARTICIPANTS = "\\(Max: ${max_participants}\\)"
    INVITED_BY_USER = "_\\(invited by ${user}\\)_"
    MEETING_WITHOUT_DESCRIPTION = "_This meeting has no description yet_"
    MEETING_WITHOUT_PARTICIPANTS = "_This meeting has no participants yet_"

    # Join and Leave messages
    JOINED_MEETING_SUCCESS = "You joined the meeting!"
    JOINED_MEETING_ALREADY = "You have already joined this meeting"
    JOINED_MEETING_NOT_FOUND = "The meeting you tried to join does not exist"
    JOINED_MEETING_FULL = "Sorry! The meeting is full"
    JOINED_MEETING_FULL_WAITING_LIST = "The meeting is full. You have been added to the waiting list."
    JOINED_MEETING_UNREGISTERED = (
        "You have joined the meeting, %{user}! "
        "It seems you have never used Mitup before, open a chat with @mitupbot to be "
        "receive notifications and create new meetings!"
    )
    LEFT_MEETING_SUCCESS = "You have left the meeting"
    LEFT_MEETING_ALREADY = "You cannot leave a meeting you have not joined"
    LEFT_MEETING_NOT_FOUND = "The meeting you tried to leave does not exist"
    LEFT_MEETING_UNREGISTERED = (
        "You have left the meeting, %{user}! "
        "It seems you have never used Mitup before, open a chat with @mitupbot to "
        "receive notifications and create new meetings!"
    )
    PROMOTED_FROM_THE_WAITING_LIST = (
        "There is an open spot in the meeting *${meeting_title}*. You have now been promoted from the waiting list!"
    )

    # On-exit prompts shown when an unexpected message interrupts a conversation
    EDIT_MEETING_TITLE_ON_EXIT = (
        "Sorry, I was expecting the title of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    EDIT_MEETING_DESCRIPTION_ON_EXIT = (
        "Sorry, I was expecting the description of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    EDIT_MEETING_LOCATION_NAME_ON_EXIT = (
        "Sorry, I was expecting the name of the location. Would you like to send it? If not, tap Cancel to exit."
    )
    EDIT_MEETING_LOCATION_COORDINATES_ON_EXIT = (
        "Sorry, I was expecting the location of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    EDIT_MEETING_MAX_PARTICIPANTS_ON_EXIT = (
        "Sorry, I was expecting the maximum number of participants. "
        "Would you like to send it? If not, tap Cancel to exit."
    )
    EDIT_MEETING_KICK_OUT_ON_EXIT = (
        "Sorry, I was expecting your selection for removing a participant. If not, tap Cancel to exit."
    )
    EDIT_MEETING_TIME_ON_EXIT = (
        "Sorry, I was expecting the new time for your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    INVITE_USER_ON_EXIT = (
        "Sorry, I was expecting the name of the person you want to invite. "
        "Would you like to send it? If not, tap Cancel to exit."
    )

    # Invite users
    INVITE_USER_PROMPT = "*Add to Guest List*\n\nPlease reply with the name of the person you want to add."
    INVITE_USER_CONFIRMATION = (
        "*Confirm Addition*\n\nAre you sure you want to add *${name}* to the meeting *${meeting_title}*?"
    )
    INVITE_USER_OPEN_CHAT = "Start Private Chat\n\nTo add participants, you need to open a chat with me first."
    INVITE_USER_GO_PRIVATE = "Continue in Private\n\nPlease switch to our private chat to send me the name."
    INVITE_USER_SUCCESS = "*List Updated*\n\nUser *${name}* has been added to the meeting *${meeting_title}*."
    INVITE_USER_MEETING_FULL = (
        "*No Spots Left*\n\nThe guest list is currently full. You cannot add anyone else unless a spot opens up."
    )
    INVITE_USER_INVITES_DISABLED = (
        "*Guest List Closed*\n\n"
        "New additions are no longer allowed for this meeting. The organizer has closed the list."
    )
    INVITE_USERS_MEETING_NOT_FOUND = (
        "*That Was Unexpected...*\n\n"
        "I suddenly lost track of the meeting details. This shouldn't happen.\n\n"
        "Please try sending the name again."
    )
    INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK = (
        "*Meeting Not Found*\n\nThe meeting you are trying to invite someone to does not exist anymore."
    )
    INVITE_USERS_CANCELED = "The invitation process has been canceled."
    INVITE_USERS_UNEXPECTED_UPDATES = "Something unexpected happened while adding the invited user. Please try again."

    # Edit title and description
    EDIT_MEETING_TITLE = "This is the current title of your meeting:\n*${title}*\n\n Send me the new one"
    EDIT_MEETING_DESCRIPTION = (
        "This is the current description of your meeting:\n${description}\n\n Send me the new one"
    )
    TITLE_SET_SUCCESS = "The title has been properly set to: *${title}*"
    DESCRIPTION_SET_SUCCESS = "The description has been properly set to: *${description}*"

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
    LOCATION_NAME_SET_SUCCESS = "The name of the location has been set to: *${name}*"
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
    MAX_PARTICIPANTS_SET_SUCCESS = "The maximum number of participants has been set to: *${max_participants}*"
    NO_LIMIT_PARTICIPANTS = "No limit"
    MAX_PARTICIPANTS_SET_FAIL = "The maximum number of participants must be a number greater than 0. Please, try again"
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = "These are the users that joined the meeting. Choose who you want to kick out."
    KICK_OUT_PARTICIPANT_CONFIRMATION_MESSAGE = (
        "Are you sure you want to kick out *${participant}* from the meeting *${meeting_title}*?"
    )
    PARTICIPANT_NO_LONGER_IN_MEETING = "The participant you tried to kick out is no longer in the meeting."
    PARTICIPANT_KICKED_OUT_SUCCESS = "The participant *${participant}* has been kicked out successfully."
    PARTICIPANT_KICKED_OUT_SUCCESS_NO_MORE_PARTICIPANTS = (
        "The participant *${participant}* has been kicked out successfully. There are no more participants to kick out."
    )

    # Edit meeting language
    EDIT_MEETING_LANGUAGE = (
        "Choose the language of your meeting. This will change the language used when sharing the meeting.\n\n"
        "Current language: *${language}*."
    )
    LANGUAGE_SET_SUCCESS = "The language has been set successfully."

    # Past meeting
    PAST_MEETING_DESCRIPTION = (
        "This meeting is no longer active. Reactivate it to share it again, or delete it permanently."
    )
    REACTIVATE_MEETING_SUCCESS = "The meeting has been reactivated. You can now edit and share it again."

    # Delete meeting
    DELETE_MEETING = "Are you sure you want to delete this meeting?"
    DELETE_MEETING_SUCCESS = "The meeting has been deleted successfully"
    DELETE_MEETING_DECLINE = "The meeting won't be deleted"
    ACCESS_TO_DELETED_MEETING = "This meeting has been deleted"

    # Edit meeting date and time
    EDIT_DATE = (
        f"Select the new date. Press *{ButtonMessages.DELETE_DATE}* if you want to "
        "unset the date and time of the meeting."
    )
    ADD_DATE = "Select the date."
    NEW_DATE_SET_SUCCESS = (
        "The date has been set to: *${datetime}*. To set the time press _${set_time_button}_, "
        "othwerise press _${back_edit_button}_ to go back to editing the meeting."
    )
    DATE_UPDATE_SUCCESS = "The date has been set to: *${datetime}*"
    EDIT_TIME = "Send me the time of the meeting in the format _HH:MM_"
    EDIT_TIME_SUCCESS = "The time of the meeting has been set to *${datetime}*"
    WRONG_TIME_FORMAT = (
        f"Not sure I understand that time {Emojis.THINK}...\n\n"
        "Please, send the time in the format _HH:MM_, for example _15:30_ or _09:15_"
    )
    INVALID_TIME = (
        f"My internal clock is old and cannot understand this science fiction time {Emojis.UFO}.\n\n"
        "Try again with a time that has valid hours \\(00-23\\) and minutes \\(00-59\\), "
        f"I am sure I can work with that {Emojis.BRAIN}."
    )
    DELETE_DATE_CONFIRMATION = "Are you sure you want to delete the date and time of the meeting?"
    DELETE_DATE_DECLINE = "The date and time won't be deleted"
    DATE_TIME_DELETED = "The date and time of the meeting have been deleted successfully"
    MEETING_HAS_BEEN_DELETED = f"{Emojis.PROHIB} This meeting has been deleted {Emojis.PROHIB}"
    MEETING_HAS_FINISHED = f"{Emojis.CHECK} This meeting has finished {Emojis.CHECK}"

    # Edit meeting settings
    EDIT_SETTINGS_MESSAGE = (
        "You can configure different aspects of your meeting:\n\n"
        "*Waiting list*: allow users to join the meeting even when it is full. "
        "Users joining when it is full will be added to a waiting list and added to the participants "
        "list as soon as a spot is available in the order they joined.\n\n"
        "*Public*: activate this to allow everyone that receives the meeting to share it again. "
        "Perfect to reach more people.\n\n"
        "*Open invitations*: activate this option to allow users who have joined the meeting to add friends "
        "even if those friends are not in Telegram.\n\n"
        "*Incognito*: a meeting with incognito enabled won't show the people that joined the meeting when shared. "
        "Only the number of participants will be shown. You will still be able to see the participants.\n\n"
        "*Show timezone*: meetings usually include the timezone the date and time refers of the meeting to. "
        "If you don't need this information displayed on the meeting message you can disable it here. "
        "This will also disable the _Timezone_ button shown when the meeting is shared."
    )

    # Attachment status (searchable via inline mode)
    NOT_SEARCHABLE_FOOTNOTE = f"{Emojis.SEARCH} Make this meeting searchable in this chat\\."
    SEARCHABLE_FOOTNOTE = f"{Emojis.CHECK} This meeting is now searchable in this chat\\."
    NOW_SEARCHABLE_ALERT = (
        f"{Emojis.CHECK} Now Searchable!\n\n"
        "This meeting is now attached to this chat. It will be included in your search "
        "results when you look for meetings using the bot's inline mode."
    )
    ALREADY_SEARCHABLE_ALERT = (
        f"{Emojis.CHECK} Already Searchable!\n\n"
        "This meeting was already attached to this chat from a previous share. "
        "It is already included in your search results."
    )

    # Show meeting list
    # TODO: this needs to be moved to a separate set of messages as it is not part of the meeting creation/editing
    # See https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/75
    NO_MEETINGS_FOUND = (
        "_You don't have any meetings yet.\n\nClick on _${new_meeting_button}_ in the main menu to create one._"
    )
    ACTIVE_MEETINGS_PAGE = "These are all your active meetings."
    JOINED_MEETINGS_PAGE = "These are the meetings you have joined."
    NO_JOINED_MEETINGS = "_You have not joined any meeting yet._"
    PAST_MEETINGS_PAGE = "These are all your past meetings."
    NO_PAST_MEETINGS = "_You have no past meetings yet._"


class InlineViewMessages(MessageBase):
    CREATE_NEW_MEETING_BUTTON = f"{Emojis.NEW_MEETING} Create a new meeting"
    EXPLORE_MITUP_BUTTON = f"{Emojis.ROCKET} Explore Mitup"
    MEETINGS_IN_THIS_CHAT_TITLE = f"{Emojis.SEARCH} Meetings in this chat"
    MEETINGS_IN_THIS_CHAT_DESCRIPTION = "Search for meetings shared in this chat"
    MEETINGS_IN_THIS_CHAT_MESSAGE = "Tap the button below to load meetings shared in this chat."
    READY_TO_SEARCH_MESSAGE = "Tap the button below to search for meetings shared in this chat."
    NO_MEETINGS_FOUND_TITLE = "No meetings found"
    NO_MEETINGS_FOUND_DESCRIPTION = "No meetings have been shared in this chat yet"
    NO_MEETINGS_FOUND_MESSAGE = "_No meetings have been shared in this chat yet._"


class NotificationMessages(MessageBase):
    MEETING_WILL_BE_PERMANENTLY_DELETED = (
        "The meeting *${meeting_title}* will be permanently deleted in *${days_until_deletion} days*.\n\n"
        "To prevent this from happening, you can reactivate the meeting by selecting "
        "the *${reactivate_meeting_button}* button below.\n\n"
        "Remember that you can always reactivate any past meeting from the *${past_meetings_button}* button in the "
        "main menu.\n\n"
        "If you do not want to reactivate the meeting, you can ignore this message."
    )
    MEETING_PERMANENTLY_DELETED = "The meeting *${meeting_title}* has been permanently deleted."
    MEETING_STARTING = "The meeting _*${meeting_title}*_ is starting soon!"


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


class Languages(MessageBase):
    SPANISH = "🇪🇸 Spanish"
    GALICIAN = "🇪🇸 Galician"
    ENGLISH = "🇺🇸 English"
    GERMAN = "🇩🇪 German"
    PORTUGUESE = "🇧🇷 Portuguese"
    ITALIAN = "🇮🇹 Italian"


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
