from .registry import HandlersRegistry
from .conversations_states import Conversation_Settings_State


HandlersRegistry.register_conversation_handler(
    "register_user_conversation_start",
    entry_points_handler_names=["start_command_with_new_user"],
    states={
        Conversation_Settings_State.TIMEZONE: ["set_timezone_settings"],
    },
    fallbacks=["cancel_command"],
)
