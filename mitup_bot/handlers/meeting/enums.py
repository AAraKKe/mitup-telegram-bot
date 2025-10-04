from enum import auto

from mitup_bot.handler_id import HandlerId


class MeetingHandlerId(HandlerId):
    CREATE_MEETING_CALLBACK = auto()
    CREATE_MEETING_CONVERSATION = auto()
    CREATE_MEETING_TITLE_MESSAGE = auto()
    CREATE_MEETING_TITLE_INVALID = auto()
    CREATE_MEETING_CANCEL_CALLBACK = auto()
    SHOW_MEETING_CALLBACK = auto()
    DELETE_MEETING_CALLBACK = auto()
    CONFIRM_DELETE_MEETING_CALLBACK = auto()
    DECLINE_DELETE_MEETING_CALLBACK = auto()
