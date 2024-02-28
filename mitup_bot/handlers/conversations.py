from enum import auto

from .callback_query import CallbackQueryId
from .commands import CommandsId
from .conversations_states import ConversationMeetingState, ConversationSettingsState
from .messages import MessagesId
from .registry import CallbackId, HandlersRegistry


class ConversationId(CallbackId):
    CONVERSATION_NEW_USER_START = auto()
    CONVERSATION_CHANGE_USER_SETTINGS = auto()
    CONVERSATION_CREATE_MEETING = auto()


HandlersRegistry.register_conversation_handler(
    ConversationId.CONVERSATION_NEW_USER_START,
    entry_points_handler_names=[CommandsId.COMMAND_START_WITH_NO_USER],
    states={
        ConversationSettingsState.TIMEZONE: [MessagesId.MESSAGE_SET_REGISTRATION_TIMEZONE],
    },
    fallbacks=[
        CommandsId.COMMAND_CANCEL,
        MessagesId.MESSAGE_WITHOUT_TEXT,  # Later on we will be able to send location as well
    ],
)

HandlersRegistry.register_conversation_handler(
    ConversationId.CONVERSATION_CHANGE_USER_SETTINGS,
    entry_points_handler_names=[CallbackQueryId.SETTINGS_TIMEZONE],
    states={
        ConversationSettingsState.TIMEZONE: [
            MessagesId.MESSAGE_SET_SETTINGS_TIMEZONE,
            CallbackQueryId.CALLBACK_QUERY_CANCEL_SETTINGS,
        ],
    },
    fallbacks=[
        CommandsId.COMMAND_CANCEL,
        MessagesId.MESSAGE_WITHOUT_TEXT,  # Later on we will be able to send location as well
    ],
)

HandlersRegistry.register_conversation_handler(
    ConversationId.CONVERSATION_CREATE_MEETING,
    entry_points_handler_names=[CallbackQueryId.CALLBACK_QUERY_CREATE_MEETING],
    states={
        ConversationMeetingState.TITLE: [
            MessagesId.MESSAGE_CREATE_MEETING,
            CallbackQueryId.CALLBACK_QUERY_CANCEL_MEETING,
        ],
    },
    fallbacks=[CommandsId.COMMAND_CANCEL, MessagesId.MESSAGE_WITHOUT_TEXT],
)
