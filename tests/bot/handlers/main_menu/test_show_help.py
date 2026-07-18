from telegram import Update

from mitup_bot.handlers.main_menu.show_help import callback_query_help
from mitup_bot.models import User
from mitup_bot.views import RenderContext, factory
from tests.helpers import StubMitupContext
from tests.helpers.stub_db import MockDbSession


async def test_help_callback_shows_help_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_help(update, context)

    context.api.assert_edit_message_called(update, factory.help_view(RenderContext(lang=user_with_settings.lang)))
