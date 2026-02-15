from enum import auto

from mitup_bot.handler_id import HandlerId

SEARCH_QUERY_PREFIX = "search:"


class InlineQueryId(HandlerId):
    INLINE_VIEW = auto()
    SHARE_MEETING = auto()
    LOAD_CHAT_MEETINGS = auto()
    SEARCH_CHAT_MEETINGS = auto()
