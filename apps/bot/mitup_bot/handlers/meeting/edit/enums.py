from enum import Enum, auto

from mitup_bot.handler_id import HandlerId


class EditMeetingHandlerId(HandlerId):
    EDIT = auto()

    # Edit meeting title
    TITLE_CALLBACK = auto()
    TITLE_MESSAGE = auto()
    TITLE_RICH_MESSAGE = auto()
    TITLE_CONVERSATION = auto()

    # Edit meeting description
    DESCRIPTION_CALLBACK = auto()
    DESCRIPTION_MESSAGE = auto()
    DESCRIPTION_RICH_MESSAGE = auto()
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
    LOCATION_NAME_RICH_MESSAGE = auto()
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

    # When — the meeting's start
    OPEN_START_EDITOR = auto()
    REOPEN_START_EDITOR = auto()
    NAVIGATE_START_CALENDAR = auto()
    PICK_START_DATE = auto()
    OPEN_START_TIME_PROMPT = auto()
    TYPE_START_DATETIME = auto()
    TYPE_START_TIME = auto()
    REJECT_START_DATETIME = auto()
    REJECT_START_TIME = auto()
    CANCEL_START_EDIT = auto()
    START_EDITOR_CONVERSATION = auto()

    # When — the meeting's end
    OPEN_END_EDITOR = auto()
    REOPEN_END_EDITOR = auto()
    NAVIGATE_END_CALENDAR = auto()
    PICK_END_DATE = auto()
    OPEN_END_TIME_PROMPT = auto()
    TYPE_END_DATETIME = auto()
    TYPE_END_TIME = auto()
    REJECT_END_DATETIME = auto()
    REJECT_END_TIME = auto()
    CANCEL_END_EDIT = auto()
    END_EDITOR_CONVERSATION = auto()

    # Edit meeting language
    LANGUAGE_CALLBACK = auto()
    SET_LANGUAGE_CALLBACK = auto()

    # Edit meeting settings
    MEETING_SETTINGS_CALLBACK = auto()
    SET_MEETING_WAITING_LIST_CALLBACK = auto()
    SET_MEETING_PUBLIC_CALLBACK = auto()
    SET_MEETING_ALLOW_INVITATIONS_CALLBACK = auto()
    SET_MEETING_INCOGNITO_CALLBACK = auto()

    # Cancel button during edit
    CANCEL = auto()


class ConversationMeetingState(Enum):
    EDIT_TITLE = auto()
    EDIT_DESCRIPTION = auto()
    EDIT_MAX_PARTICIPANTS = auto()
    EDIT_LOCATION_NAME = auto()
    EDIT_LOCATION_COORDIANTES = auto()

    # When — each state is named after the screen the owner is looking at
    START_EDITOR = auto()
    START_CALENDAR = auto()
    START_TIME_PROMPT = auto()
    END_EDITOR = auto()
    END_CALENDAR = auto()
    END_TIME_PROMPT = auto()
