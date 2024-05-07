from enum import Enum, auto

from mitup_bot.callback_id import CallbackId


class RegistrationProcessHandlerId(CallbackId):
    # Edit registration timezone
    TIMEZONE_COMMAND = auto()
    TIMEZONE_MESSAGE_WITH_TEXT = auto()
    TIMEZONE_MESSAGE_WITH_LOCATION = auto()
    TIMEZONE_CONVERSATION = auto()


class ConversationRegistrationProcessState(Enum):
    TIMEZONE = auto()
