import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState, EditSettingsHandlerId
from mitup_bot.limits import MEETING_MAX_TIMEOUT_MINUTES
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages
from mitup_bot.views import RenderContext, factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    telegram_user_from_user,
)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_TIMEOUT)], indirect=True)
async def test_callback_query_timeout(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TIMEOUT_CALLBACK, handler_context=handler_context)

    expected_view = factory.change_settings_element_view(
        RenderContext(lang=user_with_settings.lang),
        message=SettingsMessages.TIMEOUT_PROMPT.get(
            lang=user_with_settings.lang,
            timeout=user_with_settings.settings.timeout,
            max_timeout=MEETING_MAX_TIMEOUT_MINUTES,
        ),
    )

    context.api.assert_edit_message_called(update, expected_view)
    assert result == ConversationSettingsState.TIMEOUT

    # The prompt states the ceiling with a substituted number, not a leftover placeholder
    rendered = context.api.call_args("edit_message").kwargs["view"].description.text
    assert str(MEETING_MAX_TIMEOUT_MINUTES) in rendered
    assert "${" not in rendered


@pytest.mark.parametrize(
    "update, timeout",
    [
        (UpdateRequest(message_text="10"), 10),
        (UpdateRequest(message_text=str(MEETING_MAX_TIMEOUT_MINUTES)), MEETING_MAX_TIMEOUT_MINUTES),
    ],
    ids=["ordinary_value", "at_cap"],
    indirect=["update"],
)
async def test_settings_timeout_text_message_handler(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    timeout: int,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(
        EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT, handler_context=handler_context
    )

    expected_view = factory.settings_view(
        RenderContext(lang=user_with_settings.lang),
        message=SettingsMessages.TIMEOUT_SUCCESS.get(lang=user_with_settings.lang, timeout=timeout),
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.timeout == timeout
    context.api.assert_send_message_called(update, expected_view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(message_text="invalid"),
        UpdateRequest(message_text="-5"),
        UpdateRequest(message_text="5.5"),
        UpdateRequest(message_text=str(MEETING_MAX_TIMEOUT_MINUTES + 1)),
        UpdateRequest(message_text="99999999999"),
    ],
    ids=["invalid_text", "negative_number", "decimal_number", "above_cap", "far_above_cap"],
    indirect=True,
)
async def test_settings_timeout_invalid_input_handler(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    app: StubMitupApp,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    stored_timeout = user_with_settings.settings.timeout

    # Frist call the conversation handler with the valid callback
    telegram_user = telegram_user_from_user(user_with_settings)
    ctx = HandlerContext(
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
        metrics_client=handler_context.metrics_client,
    )
    context, _ = await call_handler(
        EditSettingsHandlerId.TIMEOUT_CONVERSATION,
        handler_context=ctx,
    )

    # Now that we are in the conversation, we will call the conversation handler with a text message with invalid input
    context, _ = await call_handler(EditSettingsHandlerId.TIMEOUT_CONVERSATION, handler_context=handler_context)

    expected_view = factory.change_settings_element_view(
        RenderContext(lang=user_with_settings.lang),
        message=SettingsMessages.TIMEOUT_INVALID.get(
            lang=user_with_settings.lang, max_timeout=MEETING_MAX_TIMEOUT_MINUTES
        ),
    )

    # Tow messages are sent, one to request the timeout and another one to inform the user that the input was invalid
    context.api.assert_send_message_called(update, expected_view)

    # The rejection states the ceiling with a substituted number, not a leftover placeholder
    rendered = context.api.call_args("send_message").kwargs["view"].description.text
    assert str(MEETING_MAX_TIMEOUT_MINUTES) in rendered
    assert "${" not in rendered

    # Rejected input never reaches the setting, whether it is not a number or above the cap
    assert user_with_settings.settings.timeout == stored_timeout

    # After failing we should still be on the proper state, send now a valid message
    assert update.effective_message is not None
    correct_message = Message(
        1,
        from_user=update.effective_message.from_user,
        date=update.effective_message.date,
        chat=update.effective_message.chat,
        text="10",
    )

    ctx = HandlerContext(
        update=Update(1, message=correct_message), app=app, metrics_client=handler_context.metrics_client
    )
    context, _ = await call_handler(
        EditSettingsHandlerId.TIMEOUT_CONVERSATION,
        handler_context=ctx,
    )

    assert user_with_settings.settings.timeout == 10
