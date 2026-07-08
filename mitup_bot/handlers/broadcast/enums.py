from enum import Enum, auto

from mitup_bot.handler_id import HandlerId


class BroadcastHandlerId(HandlerId):
    BROADCAST_OPEN_CALLBACK = auto()
    BROADCAST_CONVERSATION = auto()
    BROADCAST_CONTENT_MESSAGE = auto()
    BROADCAST_INVALID_CONTENT_MESSAGE = auto()
    BROADCAST_CONFIRM_CALLBACK = auto()
    BROADCAST_CANCEL_CALLBACK = auto()


class ConversationBroadcastState(Enum):
    AWAITING_CONTENT = auto()
