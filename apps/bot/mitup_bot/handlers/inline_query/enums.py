from enum import StrEnum, auto

from mitup_bot.handler_id import HandlerId

SEARCH_QUERY_PREFIX = "search:"


class InlineQueryId(HandlerId):
    INLINE_VIEW = auto()
    SHARE_MEETING = auto()
    LOAD_CHAT_MEETINGS = auto()
    SEARCH_CHAT_MEETINGS = auto()
    SHARED_MEETING = auto()


class ShareRejectionReason(StrEnum):
    """Machine key telling the four inline-share rejections apart under one event name.

    The query is answered identically for all four, so nothing on the wire says whether an id names
    a meeting somebody else keeps private.
    """

    NON_NUMERIC_INLINE_QUERY = auto()
    MEETING_NOT_FOUND = auto()
    MEETING_NOT_SHAREABLE = auto()
    MEETING_FINISHED = auto()
