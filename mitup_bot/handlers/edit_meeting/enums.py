from enum import auto

from mitup_bot.callback_id import CallbackId


class EditMeetinHandlerId(CallbackId):
    EDIT = auto()
    # Edit meeting location
    LOCATION_CALLBACK = auto()
    LOCATION_NAME_CALLBACK = auto()
    LOCATION_NAME_CONVERSATION = auto()
    LOCATION_NAME_MESSAGE = auto()
    LOCATION_COORDINATES_CALLBACK = auto()
    LOCATION_COORDINATES_CONVERSATION = auto()
    LOCATION_COORDINATES_MESSAGE = auto()
    LOCATION_COORDINATES_WRONG_MESSAGE = auto()
    CANCEL = auto()
