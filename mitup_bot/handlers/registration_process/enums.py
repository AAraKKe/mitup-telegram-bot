from enum import Enum, auto

from mitup_bot.callback_id import CallbackId


class RegistrationProcessHandlerId(CallbackId):
    # Edit registration timezone
    REGISTRATION_TIMEZONE_COMMAND = auto()
    REGISTRATION_TIMEZONE_MESSAGE_WITH_TEXT = auto()
    REGISTRATION_TIMEZONE_MESSAGE_WITH_LOCATION = auto()
    REGISTRATION_TIMEZONE_CONVERSATION = auto()


class ConversationRegistrationProcessState(Enum):
    TIMEZONE = auto()
