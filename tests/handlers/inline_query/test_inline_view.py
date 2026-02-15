import pytest
from telegram import Update
from telegram.ext import Application

from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import User
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import InlineViewMessages
from mitup_bot.views import InlineResultsButton
from tests.helpers.context import call_handler
from tests.helpers.fixtures import UpdateRequest
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_returns_results_and_button(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    mock_session.add_user(user_with_settings)

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_method_just_called("answer_inline_query")
    _, kwargs = context.api.call_args("answer_inline_query")

    # Verify the button is an InlineResultsButton with a start_parameter
    button = kwargs.get("button")
    assert isinstance(button, InlineResultsButton)
    assert button.start_parameter == "inline"

    # Verify results contain the dummy "meetings in this chat" article
    results = kwargs.get("results")
    assert results is not None
    assert len(results) == 1
    assert results[0].id == "meetings_in_this_chat"


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_uses_user_language(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    """When the user has a mitup profile, inline view messages should use their language."""
    mock_session.add_user(user_with_settings)
    lang = user_with_settings.lang

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    _, kwargs = context.api.call_args("answer_inline_query")
    button = kwargs["button"]
    assert button.text == InlineViewMessages.CREATE_NEW_MEETING_BUTTON.get(lang=lang, plain=True)

    results = kwargs["results"]
    assert results[0].title == InlineViewMessages.MEETINGS_IN_THIS_CHAT_TITLE.get(lang=lang, plain=True)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_falls_back_to_default_language_for_unknown_user(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
):
    """When the user does not have a mitup profile, inline view should fall back to the default language."""
    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_method_just_called("answer_inline_query")
    _, kwargs = context.api.call_args("answer_inline_query")
    button = kwargs["button"]
    default_lang = TranslationEngine.FALLBACK_LANG
    assert button.text == InlineViewMessages.CREATE_NEW_MEETING_BUTTON.get(lang=default_lang, plain=True)

    # Verify result texts are also rendered using the fallback language
    results = kwargs["results"]
    assert len(results) == 1
    article = results[0]
    assert article.title == InlineViewMessages.MEETINGS_IN_THIS_CHAT_TITLE.get(lang=default_lang, plain=True)
    assert article.inline_description == InlineViewMessages.MEETINGS_IN_THIS_CHAT_DESCRIPTION.get(
        lang=default_lang, plain=True
    )
    assert article.description == InlineViewMessages.MEETINGS_IN_THIS_CHAT_MESSAGE.get(lang=default_lang)
