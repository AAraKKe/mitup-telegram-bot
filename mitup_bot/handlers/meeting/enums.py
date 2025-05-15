from enum import auto

from mitup_bot.callback_id import CallbackId


class MeetingHandlerId(CallbackId):
    CREATE_MEETING_CALLBACK = auto()
    CREATE_MEETING_CONVERSATION = auto()
    CREATE_MEETING_TITLE_MESSAGE = auto()
    CREATE_MEETING_TITLE_INVALID = auto()
    CREATE_MEETING_CANCEL_CALLBACK = auto()
    SHOW_MEETING_CALLBACK = auto()
    DELETE_MEETING_CALLBACK = auto()
    CONFIRM_DELETE_MEETING_CALLBACK = auto()
    DECLINE_DELETE_MEETING_CALLBACK = auto()
