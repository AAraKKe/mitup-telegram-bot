from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry, PositiveNumberFilter
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, SettingsMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import MitupView

from .enums import ConversationSettingsState, EditSettingsHandlerId


def notification_status(user: User) -> FormattedText:
    if user.settings.notification:
        return SettingsMessages.ENABLED.get(lang=user.lang)
    return SettingsMessages.DISABLED.get(lang=user.lang)


def edit_notification_view(user: User) -> MitupView:
    message = SettingsMessages.NOTIFICATIONS_DESCRIPTION.get(
        lang=user.lang,
        notifications_status=notification_status(user),
        notifications_time=user.settings.notification_time,
    )
    action = (ButtonMessages.DISABLE if user.settings.notification else ButtonMessages.ENABLE).get_text(lang=user.lang)
    set_time_action = ButtonMessages.NOTIFICATIONS_TIME.get_text(lang=user.lang)

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
@with_session
async def callback_query_notifications(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: `edit_notification_view` reads `user.lang`/`user.settings` and never the
    # meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session, load_collections=False)

    await context.api.edit_message(update=update, view=edit_notification_view(user))


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TOGGLE_NOTIFICATIONS,
    callback_data=cb.TOGGLE_NOTIFICATIONS,
)
@with_session
async def callback_query_toggle_notifications(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)

    user.settings.notification = not user.settings.notification
    await session.flush()

    await context.api.edit_message(update=update, view=edit_notification_view(user))


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_NOTIFICATION_TIME, callback_data=cb.SET_NOTIFICATION_TIME, bindable=False
)
@with_session
async def callback_query_set_notification_time(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)
    message = SettingsMessages.NOTIFICATIONS_TIME_PROMPT.get(lang=user.lang)

    view = views.factory.change_settings_element_view(
        guards.render_context(user, update, context), message=message, callback_data=cb.EDIT_NOTIFICATIONS
    )

    await context.api.edit_message(update=update, view=view)

    return ConversationSettingsState.NOTIFICATION_TIME


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT, PositiveNumberFilter(), bindable=False
)
@with_session
async def settings_notification_time_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
):
    user = await guards.current_user(update, session, load_collections=False)
    notification_time_str = cast(str, guards.message(update).text)

    notification_time = int(notification_time_str)

    user.settings.notification_time = notification_time
    await session.flush()

    message = SettingsMessages.NOTIFICATIONS_TIME_SUCCESS.get(
        lang=user.lang, notifications_time=user.settings.notification_time
    )
    view = edit_notification_view(user).with_context(message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditSettingsHandlerId.NOTIFICATION_TIME_INVALID_INPUT, filters=filters.ALL, bindable=False
)
@with_session
async def settings_notification_time_invalid_input_handler(
    session: AsyncSession, update: Update, context: TMitupContext
):
    user = await guards.current_user(update, session, load_collections=False)
    message = CommonMessages.POSITIVE_INTEGER_INVALID.get(lang=user.lang)

    view = views.factory.change_settings_element_view(
        guards.render_context(user, update, context), message=message, callback_data=cb.EDIT_NOTIFICATIONS
    )

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
