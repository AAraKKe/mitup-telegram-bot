from enum import Enum, StrEnum, auto

from mitup_bot.handler_id import HandlerId


class EditSettingsHandlerId(HandlerId):
    EDIT = auto()

    # Edit registration timezone
    REGISTRATION_TIMEZONE_COMMAND = auto()
    REGISTRATION_TIMEZONE_MESSAGE_WITH_TEXT = auto()
    REGISTRATION_TIMEZONE_MESSAGE_WITH_LOCATION = auto()
    REGISTRATION_TIMEZONE_CONVERSATION = auto()

    # Edit settings timezone
    TIMEZONE_CALLBACK = auto()
    TIMEZONE_MESSAGE_WITH_TEXT = auto()
    TIMEZONE_MESSAGE_WITH_LOCATION = auto()
    TIMEZONE_RICH_MESSAGE = auto()
    TIMEZONE_CONVERSATION = auto()
    CANCEL = auto()

    # Edit language
    SET_LANGUAGE_CALLBACK = auto()

    # Edit default options
    DEFAULT_OPTIONS_CALLBACK = auto()
    OPEN_DEFAULT_BEHAVIOR = auto()
    OPEN_DEFAULT_TIME_FORMAT = auto()
    SET_DEFAULT_WAITING_LIST = auto()
    SET_DEFAULT_PUBLIC = auto()
    SET_DEFAULT_INVITATIONS = auto()
    SET_DEFAULT_INCOGNITO = auto()
    SET_DEFAULT_LOCK_ON_START = auto()
    SET_DEFAULT_SHOW_TIMEZONE = auto()
    SET_DEFAULT_CLOCK_24H = auto()
    SET_DEFAULT_DATE_FORMAT = auto()

    # Edit timeout
    TIMEOUT_CALLBACK = auto()
    TIMEOUT_MESSAGE_WITH_TEXT = auto()
    TIMEOUT_INVALID_INPUT = auto()
    TIMEOUT_CONVERSATION = auto()

    # Edit notifications
    TOGGLE_NOTIFICATIONS = auto()
    TOGGLE_START_REMINDER = auto()
    TOGGLE_DELETION_WARNING = auto()
    TOGGLE_DELETION_NOTICE = auto()
    SET_NOTIFICATION_TIME = auto()
    NOTIFICATION_TIME_MESSAGE_WITH_TEXT = auto()
    NOTIFICATION_TIME_INVALID_INPUT = auto()
    NOTIFICATION_CONVERSATION = auto()


class ConversationSettingsState(Enum):
    TIMEZONE = auto()
    TIMEOUT = auto()
    NOTIFICATION_TIME = auto()


class SettingName(StrEnum):
    """The `setting` facet of the shared `User setting changed` event.

    Every member doubles as the `Settings` attribute it writes, which is what lets the boolean
    toggles share one call site.
    """

    LANGUAGE = auto()
    TIMEZONE = auto()
    TIMEOUT = auto()
    NOTIFICATION = auto()
    NOTIFICATION_TIME = auto()
    DELETION_WARNING = auto()
    DELETION_NOTICE = auto()
    DEFAULT_WAITING_LIST = auto()
    DEFAULT_PUBLIC = auto()
    DEFAULT_ALLOW_INVITATION = auto()
    DEFAULT_INCOGNITO = auto()
    DEFAULT_LOCK_ON_START = auto()
    DEFAULT_SHOW_TIMEZONE = auto()
    DEFAULT_CLOCK_24H = auto()
    DEFAULT_DATE_FORMAT = auto()
