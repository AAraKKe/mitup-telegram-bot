from enum import Enum, auto


class ConversationSettingsState(Enum):
    CHOOSE_SETTINGS = auto()
    TIMEZONE = auto()


class ConversationMeetingState(Enum):
    TITLE = auto()
