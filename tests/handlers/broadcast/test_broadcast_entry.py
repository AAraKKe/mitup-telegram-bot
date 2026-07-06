from typing import TYPE_CHECKING

import pytest
from telegram import Update

from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId, ConversationBroadcastState
from mitup_bot.models import User
from mitup_bot.utils.messages import BroadcastOperatorMessages
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler, create_bot_config

if TYPE_CHECKING:
    from tests.helpers.types import RegisterMember, StashBotConfig

ADMIN_TG_ID = 123  # matches the default update sender (DEFAULT_USER_ID)


@pytest.mark.parametrize("update", [UpdateRequest(command="broadcast")], indirect=True)
async def test_broadcast_command_admin_opens_flow(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    stash_bot_config: StashBotConfig,
    register_member: RegisterMember,
):
    stash_bot_config(create_bot_config([ADMIN_TG_ID]))
    register_member(user_with_settings)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_COMMAND, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(
        update, BroadcastOperatorMessages.UPLOAD_PROMPT.get(lang=user_with_settings.lang)
    )


@pytest.mark.parametrize("update", [UpdateRequest(command="broadcast")], indirect=True)
async def test_broadcast_command_non_admin_is_silent(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    stash_bot_config: StashBotConfig,
    register_member: RegisterMember,
):
    """A member who is not on the allowlist gets no reply and the conversation never starts."""
    stash_bot_config(create_bot_config([999]))
    register_member(user_with_settings)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_COMMAND, handler_context=handler_context)

    assert state == -1  # ConversationHandler.END
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(command="broadcast")], indirect=True)
async def test_broadcast_command_non_member_is_silent(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    stash_bot_config: StashBotConfig,
):
    """A non-member (no MEMBER row) is on the allowlist by id but still gets nothing — the guard
    requires an actual reachable member row before the feature reveals itself."""
    stash_bot_config(create_bot_config([ADMIN_TG_ID]))
    # Deliberately do NOT register a member row.

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_COMMAND, handler_context=handler_context)

    assert state == -1  # ConversationHandler.END
    context.api.assert_send_message_not_called()
