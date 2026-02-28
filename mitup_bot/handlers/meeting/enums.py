from enum import Enum, auto

from mitup_bot.handler_id import HandlerId


class MeetingHandlerId(HandlerId):
    CREATE_MEETING_CALLBACK = auto()
    CREATE_MEETING_CONVERSATION = auto()
    CREATE_MEETING_TITLE_MESSAGE = auto()
    CREATE_MEETING_CANCEL_CALLBACK = auto()
    SHOW_MEETING_CALLBACK = auto()
    DELETE_MEETING_CALLBACK = auto()
    CONFIRM_DELETE_MEETING_CALLBACK = auto()
    DECLINE_DELETE_MEETING_CALLBACK = auto()

    # Past meeting handlers
    SHOW_PAST_MEETING_CALLBACK = auto()
    REACTIVATE_MEETING_CALLBACK = auto()
    DELETE_PAST_MEETING_CALLBACK = auto()
    CONFIRM_DELETE_PAST_MEETING_CALLBACK = auto()
    DECLINE_DELETE_PAST_MEETING_CALLBACK = auto()

    # Join and leave actions
    JOIN = auto()
    LEAVE = auto()

    # Attach to chat
    ATTACH_TO_CHAT = auto()

    # Invite users
    INVITE_USERS_CALLBACK = auto()
    INVITE_USERS_CONVERSATION = auto()
    INVITE_USERS_NAME_MESSAGE = auto()
    INVITE_USERS_CONFIRM_CALLBACK = auto()
    INVITE_USERS_DECLINE_CALLBACK = auto()
    INVITE_USERS_CANCEL_CALLBACK = auto()
    INVITE_USERS_FALLBACK = auto()


class ConversationMeetingState(Enum):
    TITLE = auto()


class ConversationInviteState(Enum):
    NAME = auto()
    CONFIRMATION = auto()
