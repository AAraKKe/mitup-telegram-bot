from typing import TYPE_CHECKING

import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.broadcast.entry import upload_prompt_view
from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId, ConversationBroadcastState
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_bot_config,
    create_broadcast,
)

if TYPE_CHECKING:
    from tests.helpers.types import RegisterAuthorDrafts, RegisterMember, StashBotConfig

ADMIN_TG_ID = 123  # matches the default update sender (DEFAULT_USER_ID)

BROADCAST_UPDATE = pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.BROADCAST)], indirect=True)


@BROADCAST_UPDATE
async def test_broadcast_button_admin_opens_flow(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    stash_bot_config: StashBotConfig,
    register_member: RegisterMember,
):
    stash_bot_config(create_bot_config([ADMIN_TG_ID]))
    register_member(user_with_settings)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    # Edits the admin-menu message in place (rather than sending a new one) into the upload prompt.
    context.api.assert_edit_message_called(update, upload_prompt_view(user_with_settings.lang))


@BROADCAST_UPDATE
async def test_broadcast_button_discards_existing_drafts(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    stash_bot_config: StashBotConfig,
    register_member: RegisterMember,
    register_author_drafts: RegisterAuthorDrafts,
):
    stash_bot_config(create_bot_config([ADMIN_TG_ID]))
    register_member(user_with_settings)
    prior_draft = create_broadcast(id=77, name="old", author_tg_id=ADMIN_TG_ID)
    register_author_drafts(ADMIN_TG_ID, (prior_draft,))

    await call_handler(BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, handler_context=handler_context)

    mock_session.assert_deleted(prior_draft)


@BROADCAST_UPDATE
async def test_broadcast_button_non_admin_is_dropped(
    update: Update,
    handler_context: HandlerContext,
    stash_bot_config: StashBotConfig,
):
    """A forged callback from a non-admin never runs the entry: the feature stays invisible."""
    stash_bot_config(create_bot_config([999]))

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, handler_context=handler_context)

    assert state is None
    context.api.assert_edit_message_not_called()


@BROADCAST_UPDATE
async def test_broadcast_button_admin_without_member_row_ends_silently(
    update: Update,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    stash_bot_config: StashBotConfig,
):
    """An admin id that resolves to no reachable member row ends the flow without a reply, rather
    than letting the operator load crash."""
    stash_bot_config(create_bot_config([ADMIN_TG_ID]))
    # Deliberately do NOT register a member row.

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_edit_message_not_called()


def test_upload_prompt_view_has_cancel_button():
    view = upload_prompt_view("en")

    buttons = [button for row in view.keyboard for button in row]
    assert any(button.callback_data == cb.CANCEL_BROADCAST for button in buttons)
