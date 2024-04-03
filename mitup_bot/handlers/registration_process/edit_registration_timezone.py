import logging
from typing import cast

from sqlmodel import Session
from telegram import Location, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards, timezone_api
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.handlers.commands import CommandsId
from mitup_bot.handlers.personal_filters import UserExistFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Settings, User
from mitup_bot.utils import SettingsMessages
from mitup_bot.views import factory

from .enums import ConversationRegistrationProcessState, RegistrationProcessHandlerId


@HandlersRegistry.register_command(
    RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_COMMAND,
    command="start",
    filters=~UserExistFilter(),
    bindable=False,
)
@with_async_session
async def command_start_with_new_user(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into command_start_with_new_user")

    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    user = User(
        first_name=update.effective_user.first_name,
        tg_user_id=update.effective_user.id,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        settings=Settings(),
    )
    session.add(user)
    message = SettingsMessages.SET_REGISTRATION_TIMEZONE.get(first_name=user.first_name)

    await api.send_message(context, update, message)

    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT, bindable=False
)
@with_async_session
async def registration_timezone_text_message_handler(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into registration_timezone_text_message_handler")

    user = guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address)) is None:
        logging.warning(f"The user {user.id} tried to set a timezone {address} that is not correct. Trying again")

        await api.send_message(context, update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get())

        return ConversationRegistrationProcessState.TIMEZONE

    print(f"\n\n\n {new_timezone} \n\n\n")
    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view().with_context(message)

    await api.send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@with_async_session
async def registration_timezone_location_message_handler(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into registration_timezone_location_message_handler")

    user = guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude)) is None:
        logging.warning(f"The user {user.id} tried to set a location {location} that is not correct. Trying again")

        await api.send_message(context, update, SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get())

        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view().with_context(message)

    await api.send_message(context, update, view)

    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_CONVERSATION,
    entry_points_handler_names=[RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_COMMAND],
    states={
        ConversationRegistrationProcessState.TIMEZONE: [
            RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_MESSAGE_WITH_TEXT,
            RegistrationProcessHandlerId.REGISTRATION_TIMEZONE_MESSAGE_WITH_LOCATION,
        ],
    },
    fallbacks=[CommandsId.CANCEL],
)
