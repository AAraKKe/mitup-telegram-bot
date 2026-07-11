import pytest
from telegram import Update

from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import GridMitupView, factory
from tests.helpers import AnyFloat, HandlerContext, MockDbSession, UpdateRequest, call_handler
from tests.helpers.monitoring import MetricAssertions


def language_buttons(lang: str) -> list[ButtonConfig]:
    return [
        ButtonConfig(text=factory.LANGUAGE_BUTTONS[language].get(lang=lang), callback_data=cb.SET_LANGUAGE.with_id(idx))
        for idx, language in enumerate(SUPPORTED_LANGUAGES)
    ]


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_LANGUAGE)], indirect=True)
async def test_edit_language(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, _ = await call_handler(EditSettingsHandlerId.LANGUAGE_CALLBACK, handler_context=handler_context)

    expected_view = GridMitupView(
        description=SettingsMessages.LANGUAGE_PROMPT.get(
            lang=user_with_settings.lang,
            language=factory.LANGUAGE_BUTTONS[user_with_settings.lang].get(lang=user_with_settings.lang),
        ),
        buttons=language_buttons(user_with_settings.lang),
        column_size=min(len(SUPPORTED_LANGUAGES), 3),
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.SETTINGS.back(lang=user_with_settings.lang),
                    callback_data=cb.SETTINGS,
                ),
            ]
        ]
    )

    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update,language",
    [
        [UpdateRequest(callback_query=cb.SET_LANGUAGE.with_id(idx)), lang]
        for idx, lang in enumerate(SUPPORTED_LANGUAGES)
    ],
    indirect=["update"],
    ids=[f"new_lang_{lang}" for lang in SUPPORTED_LANGUAGES],
)
async def test_set_language(
    update: Update,
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
    language: str,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, _ = await call_handler(EditSettingsHandlerId.SET_LANGUAGE_CALLBACK, handler_context=handler_context)

    expected_view = (
        GridMitupView(
            description=SettingsMessages.LANGUAGE_PROMPT.get(
                lang=language,
                language=factory.LANGUAGE_BUTTONS[language].get(lang=language),
            ),
            buttons=language_buttons(language),
            column_size=min(len(SUPPORTED_LANGUAGES), 3),
        )
        .with_context_menu(
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.SETTINGS.back(lang=language),
                        callback_data=cb.SETTINGS,
                    ),
                ]
            ]
        )
        .with_context(SettingsMessages.LANGUAGE_SUCCESS.get(lang=language))
    )

    assert user_with_settings.lang == language
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

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("InvalidLanguageError"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)
