from math import ceil

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update

from mitup_bot.exceptions import InvalidLanguageError
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import ButtonConfig, PaginatedMitupView, factory
from tests.helpers import AnyFloat, MockApi, MockDbSession, StubMitupApp, UpdateRequest, call_handler


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_settings.edit_language") as api:
        yield api


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
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, _ = await call_handler(update, app, EditSettingsHandlerId.LANGUAGE_CALLBACK)

    expected_view = PaginatedMitupView(
        description=SettingsMessages.SELECT_LANGUAGE.get(
            lang=user_with_settings.lang,
            language=factory.LANGUAGE_BUTTONS[user_with_settings.lang].get(lang=user_with_settings.lang),
        ),
        buttons=language_buttons(user_with_settings.lang),
        page_number=1,
        row_size=ceil(len(SUPPORTED_LANGUAGES) / 3),
        column_size=3,
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

    api.assert_edit_message_called(context, update, expected_view)


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
    api: MockApi,
    app: StubMitupApp,
    language: str,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, _ = await call_handler(update, app, EditSettingsHandlerId.SET_LANGUAGE_CALLBACK)

    expected_view = (
        PaginatedMitupView(
            description=SettingsMessages.SELECT_LANGUAGE.get(
                lang=language,
                language=factory.LANGUAGE_BUTTONS[language].get(lang=language),
            ),
            buttons=language_buttons(language),
            page_number=1,
            row_size=ceil(len(SUPPORTED_LANGUAGES) / 3),
            column_size=3,
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
        .with_context(SettingsMessages.LANGUAGE_SET_SUCCESS.get(lang=language))
    )

    assert user_with_settings.lang == language
    api.assert_edit_message_called(context, update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_LANGUAGE.with_id(999))], indirect=True)
async def test_set_language_fails_if_id_invalid(
    update: Update,
    mock_session: MockDbSession,
    user: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user, "tg_user_id")

    context, _ = await call_handler(update, app, EditSettingsHandlerId.SET_LANGUAGE_CALLBACK)

    context.metrics_engine.assert_metrics_emited(
        [
            MetricKey.FAULT.with_prefix("InvalidLanguageError"),
            MetricKey.FAULT,
            MetricKey.TIME,
            MetricKey.DB_CONNECTIONS_LEAKED,
        ],
        [1, 1, AnyFloat(), 0],
        [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        exception=InvalidLanguageError,
    )
