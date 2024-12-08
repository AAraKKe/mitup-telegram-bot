from enum import Enum, auto

from mitup_bot.callback_id import CallbackId


class EditSettingsHandlerId(CallbackId):
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
    TIMEZONE_CONVERSATION = auto()
    CANCEL = auto()

    # Edit language
    LANGUAGE_CALLBACK = auto()
    SET_LANGUAGE_CALLBACK = auto()

    # Edit default options
    DEFAULT_OPTIONS_CALLBACK = auto()
    SET_DEFAULT_WAITING_LIST = auto()
    SET_DEFAULT_PUBLIC = auto()
    SET_DEFAULT_INVITATIONS = auto()
    SET_DEFAULT_INCOGNITO = auto()
    SET_DEFAULT_SHOW_TIMEZONE = auto()

    # Edit timeout
    TIMEOUT_CALLBACK = auto()
    TIMEOUT_MESSAGE_WITH_TEXT = auto()
    TIMEOUT_INVALID_INPUT = auto()
    TIMEOUT_CONVERSATION = auto()

    # Edit notifications
    NOTIFICATIONS_CALLBACK = auto()
    TOGGLE_NOTIFICATIONS = auto()
    SET_NOTIFICATION_TIME = auto()
    NOTIFICATION_TIME_MESSAGE_WITH_TEXT = auto()
    NOTIFICATION_TIME_INVALID_INPUT = auto()
    NOTIFICATION_CONVERSATION = auto()


class ConversationSettingsState(Enum):
    TIMEZONE = auto()
    TIMEOUT = auto()
    NOTIFICATION_TIME = auto()
