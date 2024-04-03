import logging
from typing import cast

from sqlmodel import Session
from telegram import Location, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards, timezone_api, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers.commands import CommandsId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import ButtonMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationSettingsState, EditSettingsHandlerId


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TIMEZONE_CALLBACK, callback_data=cb.EDIT_TIEMZONE, bindable=False
)
@with_async_session
async def callback_query_timezone(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_settings_timezone")

    user = guards.current_user(update, session)
    message = SettingsMessages.SET_TIMEZONE_SETTINGS.get(timezone=user.settings.timezone)

    view = views.factory.change_settings_element_view(message)

    await api.send_message(context, update, view)

    return ConversationSettingsState.TIMEZONE


@HandlersRegistry.register_message(EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT, bindable=False)
@with_async_session
async def settings_timezone_text_message_handler(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into settings_timezone_text_message_handler")

    user = guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address)) is None:
        logging.warning(f"The user {user.id} tried to set a timezone {address} that is not correct. Trying again")

        view = MitupView(
            description=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(),
                        callback_data=cb.CANCEL_SETTINGS,
                    )
                ]
            ],
        )
        await api.send_message(context, update, view)

        return ConversationSettingsState.TIMEZONE

    user.settings.timezone = new_timezone

    session.flush()

    message = SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.settings_view(message)

    await api.send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@with_async_session
async def settings_timezone_location_message_handler(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into settings_timezone_location_message_handler")

    user = guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude)) is None:
        logging.warning(f"The user {user.id} tried to set a location {location} that is not correct. Trying again")

        view = MitupView(
            description=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(),
                        callback_data=cb.CANCEL_SETTINGS,
                    )
                ]
            ],
        )
        await api.send_message(context, update, view)

        return ConversationSettingsState.TIMEZONE

    user.settings.timezone = new_timezone

    session.flush()

    message = SettingsMessages.TIMEZONE_SETTINGS_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.settings_view(message)

    await api.send_message(context, update, view)

    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    EditSettingsHandlerId.TIMEZONE_CONVERSATION,
    entry_points_handler_names=[EditSettingsHandlerId.TIMEZONE_CALLBACK],
    states={
        ConversationSettingsState.TIMEZONE: [
            EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
            EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
            EditSettingsHandlerId.CANCEL,
        ],
    },
    fallbacks=[CommandsId.CANCEL],
)
