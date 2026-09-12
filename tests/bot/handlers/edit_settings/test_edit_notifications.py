import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.edit_settings.enums import ConversationSettingsState, EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, SettingsMessages
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views.datetime_format import duration_minutes_content
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
    "starting_state",
    [(True, True, True), (True, False, False), (False, False, True), (False, True, True)],
    ids=["all_on", "reminder_only", "notice_only", "deletion_only"],
)
async def test_callback_query_toggle_notifications_turns_everything_off_while_any_is_on(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    starting_state: tuple[bool, bool, bool],
):
    settings = user_with_settings.settings
    settings.notification, settings.deletion_warning, settings.deletion_notice = starting_state
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_NOTIFICATIONS, handler_context=handler_context)

    assert (settings.notification, settings.deletion_warning, settings.deletion_notice) == (False, False, False)
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(update, settings_card(user_with_settings))
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_NOTIFICATIONS)], indirect=True)
async def test_callback_query_toggle_notifications_turns_everything_on_when_all_are_off(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    settings = user_with_settings.settings
    settings.notification = settings.deletion_warning = settings.deletion_notice = False
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_NOTIFICATIONS, handler_context=handler_context)

    assert (settings.notification, settings.deletion_warning, settings.deletion_notice) == (True, True, True)
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(update, settings_card(user_with_settings))
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_START_REMINDER)], indirect=True)
@pytest.mark.parametrize("reminder_enabled", [True, False], ids=["enabled", "disabled"])
async def test_callback_query_toggle_start_reminder(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    reminder_enabled: bool,
):
    user_with_settings.settings.notification = reminder_enabled
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_START_REMINDER, handler_context=handler_context)

    assert user_with_settings.settings.notification is not reminder_enabled
    assert user_with_settings.settings.deletion_warning is True
    assert user_with_settings.settings.deletion_notice is True
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(update, settings_card(user_with_settings))
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_DELETION_WARNING)], indirect=True)
@pytest.mark.parametrize("warning_enabled", [True, False], ids=["enabled", "disabled"])
async def test_callback_query_toggle_deletion_warning(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    warning_enabled: bool,
):
    user_with_settings.settings.deletion_warning = warning_enabled
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_DELETION_WARNING, handler_context=handler_context)

    assert user_with_settings.settings.deletion_warning is not warning_enabled
    assert user_with_settings.settings.deletion_notice is True
    mock_session.assert_flushed()

    context.api.assert_edit_message_called(update, settings_card(user_with_settings))
    assert result is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_DELETION_NOTICE)], indirect=True)
@pytest.mark.parametrize("notice_enabled", [True, False], ids=["enabled", "disabled"])
async def test_callback_query_toggle_deletion_notice(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    notice_enabled: bool,
):
    user_with_settings.settings.deletion_notice = notice_enabled
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, result = await call_handler(EditSettingsHandlerId.TOGGLE_DELETION_NOTICE, handler_context=handler_context)

    assert user_with_settings.settings.deletion_notice is not notice_enabled
    assert user_with_settings.settings.deletion_warning is True
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
        SettingsMessages.NOTIFICATIONS_TIME_SUCCESS.rich(
            lang=user_with_settings.lang, duration=duration_minutes_content(10, lang=user_with_settings.lang)
        )
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
