from enum import auto

from mitup_bot.handler_id import HandlerId


class InlineQueryId(HandlerId):
    SHARE_MEETING = auto()
