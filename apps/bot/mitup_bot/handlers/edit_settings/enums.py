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
    LANGUAGE_CALLBACK = auto()
    SET_LANGUAGE_CALLBACK = auto()

    # Edit default options
    DEFAULT_OPTIONS_CALLBACK = auto()
    SET_DEFAULT_WAITING_LIST = auto()
    SET_DEFAULT_PUBLIC = auto()
    SET_DEFAULT_INVITATIONS = auto()
    SET_DEFAULT_INCOGNITO = auto()
    SET_DEFAULT_LOCK_ON_START = auto()

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


class SettingName(StrEnum):
    """The `setting` facet of the shared `User setting changed` event.

    The five `DEFAULT_*` members double as the `Settings` attribute they flip, which is what lets
    the default-meeting-option toggles share one call site.
    """

    LANGUAGE = auto()
    TIMEZONE = auto()
    TIMEOUT = auto()
    NOTIFICATION = auto()
    NOTIFICATION_TIME = auto()
    DEFAULT_WAITING_LIST = auto()
    DEFAULT_PUBLIC = auto()
    DEFAULT_ALLOW_INVITATION = auto()
    DEFAULT_INCOGNITO = auto()
    DEFAULT_LOCK_ON_START = auto()
