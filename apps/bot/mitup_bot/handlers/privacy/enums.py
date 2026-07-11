from enum import auto

from mitup_bot.handler_id import HandlerId


class PrivacyHandlerId(HandlerId):
    SHOW = auto()
    SEND_PRIVACY = auto()
    EXPORT_DATA = auto()
    DELETE_DATA = auto()
    CONFIRM_DELETE_DATA = auto()
    CONFIRM_DELETE_DATA_FINAL = auto()
    DECLINE_DELETE_DATA = auto()
