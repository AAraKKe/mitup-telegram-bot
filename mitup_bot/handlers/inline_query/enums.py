from enum import auto

from mitup_bot.handler_id import HandlerId


class InlineQueryId(HandlerId):
    INLINE_VIEW = auto()
    SHARE_MEETING = auto()
