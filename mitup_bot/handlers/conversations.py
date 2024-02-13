from .conversations_states import ConversationSettingsState
from .registry import HandlersRegistry

HandlersRegistry.register_conversation_handler(
    "register_user_conversation_start",
    entry_points_handler_names=["start_command_with_new_user"],
    states={
        ConversationSettingsState.TIMEZONE: ["set_first_timezone_settings"],
    },
    fallbacks=["cancel_command"],
)

HandlersRegistry.register_conversation_handler(
    "register_user_conversation_settings",
    entry_points_handler_names=["callback_query_settings_timezone"],
    states={
        ConversationSettingsState.TIMEZONE: ["set_timezone_settings"],
    },
    fallbacks=["callback_query_cancel_settings"],
)
