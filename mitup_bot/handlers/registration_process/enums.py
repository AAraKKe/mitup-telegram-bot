from enum import Enum, auto

from mitup_bot.handler_id import HandlerId

# The re-onboarding conversation binds in its own handler group, processed BEFORE group 0.
# Its /start entry decides in the handler layer (PTB filters cannot run async DB queries)
# whether the user needs onboarding: if so, every handler in the conversation raises
# ApplicationHandlerStop so group 0 never sees the update; if the user is already a MEMBER,
# the entry stays silent and the update falls through to the group-0 /start handler.
REGISTRATION_HANDLERS_GROUP = -1


class RegistrationProcessHandlerId(HandlerId):
    # Edit registration timezone
    TIMEZONE_COMMAND = auto()
    TIMEZONE_MESSAGE_WITH_TEXT = auto()
    TIMEZONE_MESSAGE_WITH_LOCATION = auto()
    TIMEZONE_INVALID_INPUT = auto()
    TIMEZONE_CONVERSATION = auto()


class ConversationRegistrationProcessState(Enum):
    TIMEZONE = auto()
