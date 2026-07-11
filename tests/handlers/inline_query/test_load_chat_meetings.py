import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.inline_query.enums import SEARCH_QUERY_PREFIX, InlineQueryId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import User
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import ButtonMessages, InlineQueryMessages
from mitup_bot.views import MitupView
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler


def expected_view(lang: str) -> MitupView:
    return MitupView(
        description=InlineQueryMessages.READY_TO_SEARCH_MESSAGE.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.SEARCH_CHAT_MEETINGS.get(lang=lang),
                    switch_inline_query_current_chat=f"{SEARCH_QUERY_PREFIX}someinstance",
                )
            ],
        ],
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.LOAD_CHAT_MEETINGS, from_bot_chat=False)],
    indirect=True,
)
async def test_load_chat_meetings_edits_with_switch_button(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """Clicking 'Load meetings' edits the message with a switch_inline_query_current_chat button."""
    mock_session.add_user(user_with_settings)

    context, _ = await call_handler(InlineQueryId.LOAD_CHAT_MEETINGS, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=expected_view(user_with_settings.lang),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.LOAD_CHAT_MEETINGS, from_bot_chat=False)],
    indirect=True,
)
async def test_load_chat_meetings_uses_fallback_language_for_unknown_user(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """When the user is not registered, the fallback language is used."""
    context, _ = await call_handler(InlineQueryId.LOAD_CHAT_MEETINGS, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=expected_view(TranslationEngine.FALLBACK_LANG),
    )
