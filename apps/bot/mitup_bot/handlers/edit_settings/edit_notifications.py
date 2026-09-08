from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry, PositiveNumberFilter
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages, SettingsMessages
from mitup_bot.views.datetime_format import duration_minutes_content

from .enums import ConversationSettingsState, EditSettingsHandlerId, SettingName
from .utils import SETTING_CHANGED_EVENT, SETTINGS_MENU_SOURCE

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TOGGLE_NOTIFICATIONS,
    callback_data=cb.TOGGLE_NOTIFICATIONS,
)
@with_session
async def callback_query_toggle_notifications(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    old_value = user.settings.notification
    user.settings.notification = not old_value
    await session.flush()

    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=SettingName.NOTIFICATION.value,
        old_value=old_value,
        new_value=user.settings.notification,
        source=SETTINGS_MENU_SOURCE,
    )

    view = views.factory.settings_view(guards.render_context(user, update, context), user)

    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_NOTIFICATION_TIME, callback_data=cb.SET_NOTIFICATION_TIME, bindable=False
)
@with_session
async def callback_query_set_notification_time(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    message = SettingsMessages.NOTIFICATIONS_TIME_PROMPT.rich(lang=user.lang)

    log.info(
        "Settings step shown",
        user_id=user.db_id,
        setting=SettingName.NOTIFICATION_TIME.value,
        current_value=user.settings.notification_time,
    )

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.edit_message(update=update, view=view)

    return ConversationSettingsState.NOTIFICATION_TIME


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT, PositiveNumberFilter(), bindable=False
)
@with_session
async def settings_notification_time_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
):
    user = await guards.current_user(update, session)
    notification_time_str = cast(str, guards.message(update).text)

    notification_time = int(notification_time_str)

    old_notification_time = user.settings.notification_time
    user.settings.notification_time = notification_time
    await session.flush()

    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=SettingName.NOTIFICATION_TIME.value,
        old_value=old_notification_time,
        new_value=notification_time,
        source=SETTINGS_MENU_SOURCE,
    )

    message = SettingsMessages.NOTIFICATIONS_TIME_SUCCESS.rich(
        lang=user.lang, duration=duration_minutes_content(user.settings.notification_time, lang=user.lang)
    )
    view = views.factory.settings_view(guards.render_context(user, update, context), user).with_context(message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_INVALID_INPUT, filters=filters.ALL, bindable=False
)
@with_session
async def settings_notification_time_invalid_input_handler(
    session: AsyncSession, update: Update, context: TMitupContext
):
    user = await guards.current_user(update, session)
    log.warning(
        "Settings step rejected input",
        user_id=user.db_id,
        setting=SettingName.NOTIFICATION_TIME.value,
        reason="not_a_positive_integer",
    )
    message = CommonMessages.POSITIVE_INTEGER_INVALID.rich(lang=user.lang)

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

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
