import pytest
from telegram import Update

from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages
from mitup_bot.views import RenderContext, factory
from tests.helpers import AnyFloat, HandlerContext, MockDbSession, UpdateRequest, call_handler
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize(
    "update,language",
    [
        [UpdateRequest(callback_query=cb.SET_LANGUAGE.with_id(idx)), lang]
        for idx, lang in enumerate(SUPPORTED_LANGUAGES)
    ],
    indirect=["update"],
    ids=[f"new_lang_{lang}" for lang in SUPPORTED_LANGUAGES],
)
async def test_set_language_answers_with_the_card_in_the_language_just_picked(
    update: Update,
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
    language: str,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, _ = await call_handler(EditSettingsHandlerId.SET_LANGUAGE_CALLBACK, handler_context=handler_context)

    assert user_with_settings.lang == language

    expected_view = factory.settings_view(RenderContext(lang=language), user_with_settings).with_context(
        SettingsMessages.LANGUAGE_SUCCESS.rich(lang=language)
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_LANGUAGE.with_id(999))], indirect=True)
async def test_set_language_fails_if_id_invalid(
    update: Update,
    mock_session: MockDbSession,
    user: User,
    handler_context: HandlerContext,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user, "tg_user_id")

    context, _ = await call_handler(EditSettingsHandlerId.SET_LANGUAGE_CALLBACK, handler_context=handler_context)

    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1, exception="InvalidLanguageError")
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)
