import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState, EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, SettingsMessages
from mitup_bot.views import MitupView, RenderContext, factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    telegram_user_from_user,
)


def settings_card(user: User) -> MitupView:
    return factory.settings_view(RenderContext(lang=user.lang), user)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_NOTIFICATIONS)], indirect=True)
@pytest.mark.parametrize(
    "notifications_enabled",
    [True, False],
    ids=["enabled", "disabled"],
)
async def test_callback_query_toggle_notifications(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    notifications_enabled: bool,
):
    user_with_settings.settings.notification = notifications_enabled
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_NOTIFICATIONS, handler_context=handler_context)

    assert user_with_settings.settings.notification is not notifications_enabled
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(update, settings_card(user_with_settings))
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_NOTIFICATION_TIME)], indirect=True)
async def test_callback_query_set_notification_time(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.SET_NOTIFICATION_TIME, handler_context=handler_context)

    expected_view = factory.change_settings_element_view(
        RenderContext(lang=user_with_settings.lang),
        message=SettingsMessages.NOTIFICATIONS_TIME_PROMPT.rich(lang=user_with_settings.lang),
    )

    context.api.assert_edit_message_called(update, expected_view)
    assert result == ConversationSettingsState.NOTIFICATION_TIME


@pytest.mark.parametrize("update", [UpdateRequest(message_text="10")], indirect=True)
async def test_settings_notification_time_text_message_handler(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(
        EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT, handler_context=handler_context
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.notification_time == 10

    expected_success_view = settings_card(user_with_settings).with_context(
        SettingsMessages.NOTIFICATIONS_TIME_SUCCESS.rich(lang=user_with_settings.lang, notifications_time=10)
    )
    context.api.assert_send_message_called(update, expected_success_view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="invalid"), UpdateRequest(message_text="-5"), UpdateRequest(message_text="5.5")],
    ids=["invalid_text", "negative_number", "decimal_number"],
    indirect=True,
)
async def test_settings_notification_time_invalid_input_handler(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    app: StubMitupApp,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # First call the conversation handler with the valid callback
    telegram_user = telegram_user_from_user(user_with_settings)
    ctx = HandlerContext(
        update=Update(
            1,
            callback_query=CallbackQuery(
                id="123",
                from_user=telegram_user,
                data=str(cb.SET_NOTIFICATION_TIME),
                chat_instance="someinstance",
                message=update.effective_message,
            ),
        ),
        app=app,
        metrics_client=handler_context.metrics_client,
    )
    context, _ = await call_handler(
        EditSettingsHandlerId.NOTIFICATION_CONVERSATION,
        handler_context=ctx,
    )

    # Now that we are in the conversation, we will call the conversation handler with a text message with invalid input
    context, _ = await call_handler(EditSettingsHandlerId.NOTIFICATION_CONVERSATION, handler_context=handler_context)

    # Check we have sent the proper message
    expected_view = factory.change_settings_element_view(
        RenderContext(lang=user_with_settings.lang),
        message=CommonMessages.POSITIVE_INTEGER_INVALID.rich(lang=user_with_settings.lang),
    )
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

    ctx = HandlerContext(
        update=Update(1, message=correct_message), app=app, metrics_client=handler_context.metrics_client
    )
    context, _ = await call_handler(
        EditSettingsHandlerId.NOTIFICATION_CONVERSATION,
        handler_context=ctx,
    )

    assert user_with_settings.settings.notification_time == 10
