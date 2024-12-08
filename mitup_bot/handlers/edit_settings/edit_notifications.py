from typing import cast

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry, PositiveNumberFilter
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import ButtonConfig, MitupView

from .enums import ConversationSettingsState, EditSettingsHandlerId


def notification_status(user: User) -> str:
    if user.settings.notification:
        return SettingsMessages.ENABLED.get(lang=user.lang)
    return SettingsMessages.DISABLED.get(lang=user.lang)


def edit_notification_view(user: User) -> MitupView:
    message = SettingsMessages.NOTIFICATIONS_SETTINGS.get(
        lang=user.lang,
        notifications_status=notification_status(user),
        notifications_time=user.settings.notification_time,
    )
    action = (ButtonMessages.DISABLE if user.settings.notification else ButtonMessages.ENABLE).get(lang=user.lang)
    set_time_action = ButtonMessages.TIME.get(lang=user.lang)

    return MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(text=action, callback_data=cb.TOGGLE_NOTIFICATIONS),
                ButtonConfig(text=set_time_action, callback_data=cb.SET_NOTIFICATION_TIME),
            ],
        ],
    ).with_back_button(ButtonMessages.SETTINGS, lang=user.lang, callback_data=cb.SETTINGS)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.NOTIFICATIONS_CALLBACK,
    callback_data=cb.EDIT_NOTIFICATIONS,
)
@with_async_session
async def callback_query_notifications(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)

    await api.edit_message(context=context, update=update, view=edit_notification_view(user))


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TOGGLE_NOTIFICATIONS,
    callback_data=cb.TOGGLE_NOTIFICATIONS,
)
@with_async_session
async def callback_query_toggle_notifications(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)

    user.settings.notification = not user.settings.notification
    session.flush()

    await api.edit_message(context=context, update=update, view=edit_notification_view(user))


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_NOTIFICATION_TIME, callback_data=cb.SET_NOTIFICATION_TIME, bindable=False
)
@with_async_session
async def callback_query_set_notification_time(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)
    message = SettingsMessages.NOTIFICATION_SET_TIME.get(lang=user.lang)

    view = views.factory.change_settings_element_view(
        lang=user.lang, message=message, callback_data=cb.EDIT_NOTIFICATIONS
    )

    await api.edit_message(context=context, update=update, view=view)

    return ConversationSettingsState.NOTIFICATION_TIME


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT, PositiveNumberFilter(), bindable=False
)
@with_async_session
async def settings_notification_time_text_message_handler(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)
    notification_time_str = cast(str, guards.message(update).text)

    notification_time = int(notification_time_str)

    user.settings.notification_time = notification_time
    session.flush()

    message = SettingsMessages.NOTIFICATION_TIME_SET_SUCCESS.get(
        lang=user.lang, notifications_time=user.settings.notification_time
    )
    view = edit_notification_view(user).with_context(message)

    await api.send_message(context=context, update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_INVALID_INPUT, filters=filters.ALL, bindable=False
)
@with_async_session
async def settings_notification_time_invalid_input_handler(session: Session, update: Update, context: MitupContext):
    user = guards.current_user(update, session)
    message = SettingsMessages.INVALID_POSITIVE_INTEGER.get(lang=user.lang)

    view = views.factory.change_settings_element_view(
        lang=user.lang, message=message, callback_data=cb.EDIT_NOTIFICATIONS
    )

    await api.send_message(context=context, update=update, view=view)

    return ConversationSettingsState.NOTIFICATION_TIME


HandlersRegistry.register_conversation_handler(
    EditSettingsHandlerId.NOTIFICATION_CONVERSATION,
    entry_points_handler_names=[EditSettingsHandlerId.SET_NOTIFICATION_TIME],
    states={
        ConversationSettingsState.NOTIFICATION_TIME: [
            EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT,
            EditSettingsHandlerId.CANCEL,
        ],
    },
    fallbacks=[EditSettingsHandlerId.NOTIFICATION_TIME_INVALID_INPUT],
)
