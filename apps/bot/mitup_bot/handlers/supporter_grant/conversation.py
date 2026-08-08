from mitup_bot.handlers.registry import HandlersRegistry

from .enums import ConversationGrantState, GrantHandlerId

HandlersRegistry.register_conversation_handler(
    GrantHandlerId.GRANT_CONVERSATION,
    entry_points_handler_names=[GrantHandlerId.GRANT_OPEN_CALLBACK],
    states={
        ConversationGrantState.AWAITING_TARGET: [
            GrantHandlerId.GRANT_TARGET_MESSAGE,
            GrantHandlerId.GRANT_CANCEL_CALLBACK,
            # Catch-all for stickers, photos and other non-command input. Must stay last so the
            # text handler claims the identifier first.
            GrantHandlerId.GRANT_INVALID_TARGET_MESSAGE,
        ],
        ConversationGrantState.AWAITING_LEVEL: [
            GrantHandlerId.GRANT_LEVEL_CALLBACK,
            GrantHandlerId.GRANT_CANCEL_CALLBACK,
        ],
        ConversationGrantState.AWAITING_CONFIRMATION: [
            GrantHandlerId.GRANT_CONFIRM_CALLBACK,
            GrantHandlerId.GRANT_CANCEL_CALLBACK,
        ],
    },
    # No fallbacks: there is no /cancel command (the Cancel button handles abandoning). Stray
    # commands fall through to the bot's global handlers, and the Host-grants admin-menu button
    # re-enters via allow_reentry.
    fallbacks=[],
)
