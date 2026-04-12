from enum import Enum, auto

from mitup_bot.handler_id import HandlerId


class EditMeetingHandlerId(HandlerId):
    EDIT = auto()

    # Edit meeting title
    TITLE_CALLBACK = auto()
    TITLE_MESSAGE = auto()
    TITLE_CONVERSATION = auto()

    # Edit meeting description
    DESCRIPTION_CALLBACK = auto()
    DESCRIPTION_MESSAGE = auto()
    DESCRIPTION_CONVERSATION = auto()

    # Edit meeting participants
    PARTICIPANTS_CALLBACK = auto()
    PARTICIPANTS_MAXIMUM_CALLBACK = auto()
    PARTICIPANTS_NO_LIMIT_CALLBACK = auto()
    PARTICIPANTS_CANCEL_CALLBACK = auto()
    PARTICIPANTS_MAXIMUM_MESSAGE = auto()
    PARTICIPANTS_MAXIMUM_CONVERSATION = auto()
    PARTICIPANTS_MAXIMUM_WRONG_MESSAGE = auto()
    PARTICIPANTS_KICK_OUT_CALLBACK = auto()
    PARTICIPANTS_KICK_OUT_ACTION_CALLBACK = auto()
    PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK = auto()

    # Edit meeting location
    LOCATION_CALLBACK = auto()
    LOCATION_NAME_CALLBACK = auto()
    LOCATION_CANCEL_CALLBACK = auto()
    LOCATION_NAME_CONVERSATION = auto()
    LOCATION_NAME_MESSAGE = auto()
    LOCATION_COORDINATES_CALLBACK = auto()
    LOCATION_COORDINATES_CONVERSATION = auto()
    LOCATION_COORDINATES_MESSAGE = auto()
    LOCATION_COORDINATES_WRONG_MESSAGE = auto()

    # When screen
    WHEN_ENTRY_CALLBACK = auto()
    CLEAR_TIMES_CALLBACK = auto()
    CONFIRM_CLEAR_TIMES_CALLBACK = auto()
    DECLINE_CLEAR_TIMES_CALLBACK = auto()
    LOCK_ON_START_CALLBACK = auto()

    # Edit meeting date and time
    DATE_TIME_ENTRY_CALLBACK = auto()
    DATE_CALLBACK = auto()
    SET_DATE_CALLBACK = auto()
    BACK_TO_EDIT_DATETIME_CALLBACK = auto()
    EDIT_TIME_CALLBACK = auto()
    EDIT_DATETIME_CONVERSATION = auto()
    SET_TIME_MESSAGE = auto()
    WRONG_TIME_FORMAT = auto()
    WRONG_TIME_MESSAGE = auto()
    DATE_TIME_ENTITY_MESSAGE = auto()
    DATETIME_WRONG_TEXT_FORMAT = auto()
    DATETIME_WRONG_MESSAGE = auto()
    CANCEL_START_TIME_CALLBACK = auto()

    # Edit meeting language
    LANGUAGE_CALLBACK = auto()
    SET_LANGUAGE_CALLBACK = auto()

    # Edit meeting settings
    MEETING_SETTINGS_CALLBACK = auto()
    SET_MEETING_WAITING_LIST_CALLBACK = auto()
    SET_MEETING_PUBLIC_CALLBACK = auto()
    SET_MEETING_ALLOW_INVITATIONS_CALLBACK = auto()
    SET_MEETING_INCOGNITO_CALLBACK = auto()

    # Edit meeting duration
    DURATION_INPUT_CALLBACK = auto()
    DURATION_CANCEL_CALLBACK = auto()
    DURATION_CONVERSATION = auto()

    # Duration — set end datetime sub-flow
    DURATION_END_ENTRY_CALLBACK = auto()
    DURATION_END_DATE_NAV_CALLBACK = auto()
    DURATION_END_SET_DATE_CALLBACK = auto()
    DURATION_END_TIME_CALLBACK = auto()
    DURATION_END_DATETIME_ENTITY_MESSAGE = auto()
    DURATION_END_WRONG_INPUT = auto()
    DURATION_END_SET_TIME_MESSAGE = auto()
    DURATION_END_TIME_WRONG_INPUT = auto()
    DURATION_BACK_TO_END_DATETIME_CALLBACK = auto()

    # Cancel button during edit
    CANCEL = auto()


class ConversationMeetingState(Enum):
    EDIT_TITLE = auto()
    EDIT_DESCRIPTION = auto()
    EDIT_MAX_PARTICIPANTS = auto()
    EDIT_LOCATION_NAME = auto()
    EDIT_LOCATION_COORDIANTES = auto()
    EDIT_DATETIME = auto()
    EDIT_DATE = auto()
    EDIT_TIME = auto()

    # Duration conversation states
    EDIT_END_DATETIME = auto()
    EDIT_END_DATE = auto()
    EDIT_END_TIME = auto()
