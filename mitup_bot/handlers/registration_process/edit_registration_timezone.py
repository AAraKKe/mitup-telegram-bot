import logging
from typing import cast

from sqlmodel import Session
from telegram import Location, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, timezone_api
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.handlers.personal_filters import UserExistFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Settings, User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import SettingsMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import ConversationRegistrationProcessState, RegistrationProcessHandlerId


@HandlersRegistry.register_command(
    RegistrationProcessHandlerId.TIMEZONE_COMMAND,
    command="start",
    filters=~UserExistFilter(),
    bindable=False,
)
@with_async_session
async def command_start_with_new_user(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into command_start_with_new_user")

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

    await context.api.send_message(update=update, view=message)

    context.put_feature_metric(Feature.NEW_LANDING)
    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT, bindable=False
)
@with_async_session
async def registration_timezone_text_message_handler(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into registration_timezone_text_message_handler")
    context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE)

    user = guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address, context)) is None:
        logging.warning(f"The user {user.db_id} tried to set a timezone {address} that is not correct. Trying again")

        await context.api.send_message(
            update=update, view=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(lang=user.lang)
        )

        context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR)
        return ConversationRegistrationProcessState.TIMEZONE

    print(f"\n\n\n {new_timezone} \n\n\n")
    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view(lang=user.lang).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR, value=0)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@with_async_session
async def registration_timezone_location_message_handler(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into registration_timezone_location_message_handler")
    context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION)

    user = guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)) is None:
        logging.warning(f"The user {user.db_id} tried to set a location {location} that is not correct. Trying again")

        await context.api.send_message(
            update=update, view=SettingsMessages.REGISTRATION_TIMEZONE_SET_FAIL.get(lang=user.lang)
        )

        context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR, value=1)
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()

    message = SettingsMessages.REGISTRATION_TIMEZONE_SET_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view(lang=user.lang).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR, value=0)
    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    RegistrationProcessHandlerId.TIMEZONE_CONVERSATION,
    entry_points_handler_names=[RegistrationProcessHandlerId.TIMEZONE_COMMAND],
    states={
        ConversationRegistrationProcessState.TIMEZONE: [
            RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
            RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
        ],
    },
    fallbacks=[],
)
