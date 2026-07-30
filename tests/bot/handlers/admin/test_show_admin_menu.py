import logging

import pytest
from telegram import Update

from mitup_bot.custom_context import BOT_CONFIG_KEY, ContextId
from mitup_bot.handlers.admin.enums import AdminHandlerId
from mitup_bot.handlers.admin.show_admin_menu import callback_query_show_admin_menu
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from tests.helpers import (
    HandlerContext,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_bot_config,
    log_record,
)
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.stub_db import MockDbSession


async def test_admin_menu_edits_to_admin_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_show_admin_menu(update, context)

    context.api.assert_edit_message_called(update, factory.admin_menu_view(RenderContext(lang=user_with_settings.lang)))


async def test_admin_menu_clears_user_data(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    await callback_query_show_admin_menu(update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.ADMIN_MENU)], indirect=True)
async def test_admin_menu_shown_for_admin_through_registry(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    handler_context.app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(AdminHandlerId.ADMIN_MENU_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.admin_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.ADMIN_MENU)], indirect=True)
async def test_admin_menu_dropped_for_non_admin_through_registry(
    update: Update,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    """A forged callback from a non-admin never runs the handler: the admin UI stays invisible."""
    handler_context.app.bot_data[BOT_CONFIG_KEY] = create_bot_config([])

    context, _ = await call_handler(AdminHandlerId.ADMIN_MENU_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.ADMIN_MENU)], indirect=True)
async def test_granted_admin_access_is_recorded_like_the_refused_one(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
):
    """The registry warns on a refused admin update; without this line the log answers who was
    turned away and never who got in."""
    caplog.set_level(logging.INFO)
    handler_context.app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])
    mock_session.add_object(user_with_settings, "tg_user_id")

    await call_handler(AdminHandlerId.ADMIN_MENU_CALLBACK, handler_context=handler_context)

    assert log_record(caplog, "Admin menu opened").__dict__["user_id"] == user_with_settings.db_id
