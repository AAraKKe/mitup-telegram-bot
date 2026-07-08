from mitup_bot.handlers.registry import HandlersRegistry

from .enums import BroadcastHandlerId, ConversationBroadcastState

HandlersRegistry.register_conversation_handler(
    BroadcastHandlerId.BROADCAST_CONVERSATION,
    entry_points_handler_names=[BroadcastHandlerId.BROADCAST_OPEN_CALLBACK],
    states={
        ConversationBroadcastState.AWAITING_CONTENT: [
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE,
            BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK,
            BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK,
            # Catch-all for stickers, photos and other non-command input. Must stay last so the
            # content handler claims documents and text first.
            BroadcastHandlerId.BROADCAST_INVALID_CONTENT_MESSAGE,
        ],
    },
    # No fallbacks: there is no /cancel command (the Cancel button handles discarding). Stray
    # commands fall through to the bot's global handlers, and the Broadcast admin-menu button
    # re-enters via allow_reentry.
    fallbacks=[],
)
