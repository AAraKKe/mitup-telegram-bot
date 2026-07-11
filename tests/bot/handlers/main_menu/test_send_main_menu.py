from telegram import Update

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.main_menu.send_main_menu import callback_query_send_main_menu
from mitup_bot.models import User
from mitup_bot.views import RenderContext, factory
from tests.helpers import StubMitupContext
from tests.helpers.stub_db import MockDbSession


async def test_send_main_menu_sends_a_new_message(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_send_main_menu(update, context)

    context.api.assert_send_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


async def test_send_main_menu_does_not_edit_the_tapped_message(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    # The button lives on a standalone message (e.g. a broadcast) that must stay in the chat, so the
    # handler posts a new message rather than editing or deleting the tapped one.
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_send_main_menu(update, context)

    context.api.assert_edit_message_not_called()


async def test_send_main_menu_preserves_user_data(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    # Navigation-only: unlike MAIN_MENU, this must not clear an unrelated in-progress conversation.
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert context.user_data is not None
    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)

    await callback_query_send_main_menu(update, context)

    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1
