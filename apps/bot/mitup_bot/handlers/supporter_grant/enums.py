from enum import Enum, auto

from mitup_bot.handler_id import HandlerId


class GrantHandlerId(HandlerId):
    GRANT_OPEN_CALLBACK = auto()
    GRANT_CONVERSATION = auto()
    GRANT_TARGET_MESSAGE = auto()
    GRANT_INVALID_TARGET_MESSAGE = auto()
    GRANT_LEVEL_CALLBACK = auto()
    GRANT_CONFIRM_CALLBACK = auto()
    GRANT_CANCEL_CALLBACK = auto()


class ConversationGrantState(Enum):
    AWAITING_TARGET = auto()
    AWAITING_LEVEL = auto()
    AWAITING_CONFIRMATION = auto()
