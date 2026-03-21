import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState, EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    telegram_user_from_user,
)


def expected_view(user: User, notifications_enabled: bool, notifications_time: int) -> MitupView:
    return MitupView(
        description=SettingsMessages.NOTIFICATIONS_SETTINGS.get(
            lang=user.lang,
            notifications_status=SettingsMessages.ENABLED.get(lang=user.lang)
            if notifications_enabled
            else SettingsMessages.DISABLED.get(lang=user.lang),
            notifications_time=notifications_time,
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DISABLE.get(lang=user.lang)
                    if notifications_enabled
                    else ButtonMessages.ENABLE.get(lang=user.lang),
                    callback_data=cb.TOGGLE_NOTIFICATIONS,
                ),
                ButtonConfig(
                    text=ButtonMessages.NOTIFICATIONS_TIME.get(lang=user.lang),
                    callback_data=cb.SET_NOTIFICATION_TIME,
                ),
            ],
        ],
    ).with_back_button(ButtonMessages.SETTINGS, lang=user.lang, callback_data=cb.SETTINGS)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_NOTIFICATIONS)], indirect=True)
@pytest.mark.parametrize(
    "notifications_enabled",
    [True, False],
    ids=["enabled", "disabled"],
)
async def test_callback_query_notifications(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    notifications_enabled: bool,
):
    user_with_settings.settings.notification = notifications_enabled
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.NOTIFICATIONS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        expected_view(user_with_settings, notifications_enabled, user_with_settings.settings.notification_time),
    )
    assert result is None


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

    user_with_settings.settings.notification = not notifications_enabled
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(
        update,
        expected_view(user_with_settings, not notifications_enabled, user_with_settings.settings.notification_time),
    )
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_NOTIFICATION_TIME)], indirect=True)
async def test_callback_query_set_notification_time(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.SET_NOTIFICATION_TIME, handler_context=handler_context)

    expected_view = factory.change_settings_element_view(
        lang=user_with_settings.lang,
        message=SettingsMessages.NOTIFICATION_SET_TIME.get(lang=user_with_settings.lang),
        callback_data=cb.EDIT_NOTIFICATIONS,
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

    expected_success_view = expected_view(
        user_with_settings, user_with_settings.settings.notification, 10
    ).with_context(
        SettingsMessages.NOTIFICATION_TIME_SET_SUCCESS.get(lang=user_with_settings.lang, notifications_time=10)
    )

    mock_session.assert_flushed()
    assert user_with_settings.settings.notification_time == 10
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
        lang=user_with_settings.lang,
        message=SettingsMessages.INVALID_POSITIVE_INTEGER.get(lang=user_with_settings.lang),
        callback_data=cb.EDIT_NOTIFICATIONS,
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
