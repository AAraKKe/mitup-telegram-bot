import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState, EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages
from mitup_bot.views import factory
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler, telegram_user_from_user


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_TIMEOUT)], indirect=True)
async def test_callback_query_timeout(
    mock_session: MockDbSession, user_with_settings: User, update: Update, app: StubMitupApp
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TIMEOUT_CALLBACK, update=update, app=app)

    expected_view = factory.change_settings_element_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.SET_TIMEOUT_SETTINGS.get(
            lang=user_with_settings.lang, timeout=user_with_settings.settings.timeout
        ),
    )

    context.api.assert_edit_message_called(update, expected_view)
    assert result == ConversationSettingsState.TIMEOUT


@pytest.mark.parametrize("update", [UpdateRequest(message_text="10")], indirect=True)
async def test_settings_timeout_text_message_handler(
    mock_session: MockDbSession, user_with_settings: User, update: Update, app: StubMitupApp
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT, update=update, app=app)

    expected_view = factory.settings_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.TIMEOUT_SET_SUCCESS.get(lang=user_with_settings.lang, timeout=10),
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timeout == 10
    context.api.assert_send_message_called(update, expected_view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="invalid"), UpdateRequest(message_text="-5"), UpdateRequest(message_text="5.5")],
    ids=["invalid_text", "negative_number", "decimal_number"],
    indirect=True,
)
async def test_settings_timeout_invalid_input_handler(
    mock_session: MockDbSession, user_with_settings: User, update: Update, app: StubMitupApp
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # Frist call the conversation handler with the valid callback
    telegram_user = telegram_user_from_user(user_with_settings)
    context, _ = await call_handler(
        EditSettingsHandlerId.TIMEOUT_CONVERSATION,
        update=Update(
            1,
            callback_query=CallbackQuery(
                id="123",
                from_user=telegram_user,
                data=str(cb.EDIT_TIMEOUT),
                chat_instance="someinstance",
                message=update.effective_message,
            ),
        ),
        app=app,
    )

    # Now that we are in the conversation, we will call the conversation handler with a text message with invalid input
    context, _ = await call_handler(EditSettingsHandlerId.TIMEOUT_CONVERSATION, update=update, app=app)

    expected_view = factory.change_settings_element_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.INVALID_POSITIVE_INTEGER.get(lang=user_with_settings.lang),
    )

    # Tow messages are sent, one to request the timeout and another one to inform the user that the input was invalid
    context.api.assert_send_message_called(update, expected_view)

    # After failing we should still be on the proper state, send now a valid message
    assert update.effective_message is not None
    correct_message = Message(
        1,
        from_user=update.effective_message.from_user,
        date=update.effective_message.date,
        chat=update.effective_message.chat,
        text="10",
    )

    context, _ = await call_handler(
        EditSettingsHandlerId.TIMEOUT_CONVERSATION, update=Update(1, message=correct_message), app=app
    )

    assert user_with_settings.settings.timeout == 10
