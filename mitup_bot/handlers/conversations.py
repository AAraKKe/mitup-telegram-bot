from enum import auto

from .callback_query import CallbackQueryId
from .commands import CommandsId
from .conversations_states import ConversationSettingsState
from .messages import MessagesId
from .registry import CallbackId, HandlersRegistry


class ConversationId(CallbackId):
    CONVERSATION_NEW_USER_START = auto()
    CONVERSATION_CHANGE_USER_SETTINGS = auto()


HandlersRegistry.register_conversation_handler(
    ConversationId.CONVERSATION_NEW_USER_START,
    entry_points_handler_names=[CommandsId.COMMAND_START_WITH_NO_USER],
    states={
        ConversationSettingsState.TIMEZONE: [MessagesId.MESSAGE_SET_REGISTRATION_TIMEZONE],
    },
    fallbacks=[CommandsId.COMMAND_CANCEL],
)

HandlersRegistry.register_conversation_handler(
    ConversationId.CONVERSATION_CHANGE_USER_SETTINGS,
    entry_points_handler_names=[CallbackQueryId.CALLBACK_QUERY_SETTINGS_TIMEZONE],
    states={
        ConversationSettingsState.TIMEZONE: [MessagesId.MESSAGE_SET_SETTINGS_TIMEZONE],
    },
    fallbacks=[CallbackQueryId.CALLBACK_QUERY_CANCEL_SETTINGS],
)
