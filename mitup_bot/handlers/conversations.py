from enum import auto

from mitup_bot.callback_id import CallbackId

from .callback_query import CallbackQueryId
from .commands import CommandsId
from .conversations_states import ConversationMeetingState
from .messages import MessagesId
from .registry import HandlersRegistry


class ConversationId(CallbackId):
    CREATE_MEETING = auto()


HandlersRegistry.register_conversation_handler(
    ConversationId.CREATE_MEETING,
    entry_points_handler_names=[CallbackQueryId.CREATE_MEETING],
    states={
        ConversationMeetingState.TITLE: [
            MessagesId.MESSAGE_CREATE_MEETING,
            CallbackQueryId.CANCEL_MEETING,
        ],
    },
    fallbacks=[CommandsId.CANCEL, MessagesId.MESSAGE_WITHOUT_TEXT],
)
