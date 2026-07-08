from collections.abc import Callable
from unittest import mock

import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId
from mitup_bot.models import Broadcast, User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.views import factory
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler, create_broadcast

ADMIN_TG_ID = 123
BROADCAST_ID = 5
BROADCAST_NAME = "Launch news"


# set_get_result is mock-session plumbing (it stubs `session.get`), not a model factory, so it stays
# local — only `create_broadcast` (model generation) belongs in the centralized helpers.
def set_get_result(mock_session: MockDbSession, broadcast: Broadcast | None):
    mock_session.get = mock.AsyncMock(return_value=broadcast)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_confirm_queues_the_draft(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_user(user_with_settings)
    broadcast = create_broadcast(id=BROADCAST_ID, name=BROADCAST_NAME, author_tg_id=ADMIN_TG_ID)
    set_get_result(mock_session, broadcast)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    assert broadcast.status is BroadcastStatus.QUEUED
    context.api.assert_edit_message_called(
        update,
        BroadcastOperatorMessages.QUEUED_CONFIRMATION.get(lang=user_with_settings.lang, name=BROADCAST_NAME),
    )
    mock_session.assert_not_deleted()


# Every shape a draft can be unavailable in: absent, owned by another operator, or already past DRAFT.
DRAFT_NOT_FOUND_CASES: list[tuple[str, Callable[[], Broadcast | None]]] = [
    ("missing", lambda: None),
    ("other_author", lambda: create_broadcast(id=BROADCAST_ID, name=BROADCAST_NAME, author_tg_id=999)),
    (
        "already_queued",
        lambda: create_broadcast(
            id=BROADCAST_ID, name=BROADCAST_NAME, author_tg_id=ADMIN_TG_ID, status=BroadcastStatus.QUEUED
        ),
    ),
]


@pytest.mark.parametrize(
    "make_broadcast",
    [pytest.param(factory, id=case_id) for case_id, factory in DRAFT_NOT_FOUND_CASES],
)
@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_confirm_reports_draft_not_found(
    mock_session: MockDbSession,
    update: Update,
    make_broadcast: Callable[[], Broadcast | None],
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_user(user_with_settings)
    set_get_result(mock_session, make_broadcast())

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(
        update, BroadcastOperatorMessages.DRAFT_NOT_FOUND.get(lang=user_with_settings.lang)
    )
    mock_session.assert_not_deleted()


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_cancel_deletes_the_draft(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_user(user_with_settings)
    broadcast = create_broadcast(id=BROADCAST_ID, name=BROADCAST_NAME, author_tg_id=ADMIN_TG_ID)
    set_get_result(mock_session, broadcast)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    mock_session.assert_deleted(broadcast)
    # Cancelling returns the operator to the admin menu with the discard confirmation prepended.
    context.api.assert_edit_message_called(
        update,
        factory.admin_menu_view(lang=user_with_settings.lang).with_context(
            BroadcastOperatorMessages.CANCELLED_CONFIRMATION.get(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_cancel_confirms_even_when_draft_already_gone(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_user(user_with_settings)
    set_get_result(mock_session, None)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(
        update,
        factory.admin_menu_view(lang=user_with_settings.lang).with_context(
            BroadcastOperatorMessages.CANCELLED_CONFIRMATION.get(lang=user_with_settings.lang)
        ),
    )
