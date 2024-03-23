from enum import auto

from mitup_bot.callback_id import CallbackId

from .callback_query import CallbackQueryId
from .commands import CommandsId
from .conversations_states import ConversationMeetingState, ConversationSettingsState
from .messages import MessagesId
from .registry import HandlersRegistry


class ConversationId(CallbackId):
    NEW_USER_START = auto()
    SETTINGS_UPDATE_TIMEZONE = auto()
    CREATE_MEETING = auto()


HandlersRegistry.register_conversation_handler(
    ConversationId.NEW_USER_START,
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
    ConversationId.SETTINGS_UPDATE_TIMEZONE,
    entry_points_handler_names=[CallbackQueryId.SETTINGS_TIMEZONE],
    states={
        ConversationSettingsState.TIMEZONE: [
            MessagesId.MESSAGE_SET_SETTINGS_TIMEZONE,
            CallbackQueryId.CANCEL_SETTINGS,
        ],
    },
    fallbacks=[
        CommandsId.COMMAND_CANCEL,
        MessagesId.MESSAGE_WITHOUT_TEXT,  # Later on we will be able to send location as well
    ],
)

HandlersRegistry.register_conversation_handler(
    ConversationId.CREATE_MEETING,
    entry_points_handler_names=[CallbackQueryId.CREATE_MEETING],
    states={
        ConversationMeetingState.TITLE: [
            MessagesId.MESSAGE_CREATE_MEETING,
            CallbackQueryId.CANCEL_MEETING,
        ],
    },
    fallbacks=[CommandsId.COMMAND_CANCEL, MessagesId.MESSAGE_WITHOUT_TEXT],
)
