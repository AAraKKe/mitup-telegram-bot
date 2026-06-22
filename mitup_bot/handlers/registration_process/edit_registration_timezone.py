import logging
from typing import cast

from sqlmodel import Session
from telegram import Location, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, timezone_api
from mitup_bot.db import with_async_session
from mitup_bot.handlers.personal_filters import MemberUserFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import RegistrationMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import ConversationRegistrationProcessState, RegistrationProcessHandlerId
from .utils import get_or_create_onboarding_user


@HandlersRegistry.register_command(
    RegistrationProcessHandlerId.TIMEZONE_COMMAND,
    command="start",
    filters=~MemberUserFilter(),
    bindable=False,
)
@with_async_session
async def command_start_with_new_user(
    session: Session, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState:
    logging.debug("Enter into command_start_with_new_user")

    user = get_or_create_onboarding_user(session, update)
    message = RegistrationMessages.TIMEZONE_PROMPT.get(first_name=user.first_name)

    await context.api.send_message(update=update, view=message)

    context.put_feature_metric(Feature.NEW_LANDING)
    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_async_session
async def registration_timezone_text_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    logging.debug("Enter into registration_timezone_text_message_handler")
    context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE)

    user = guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address, context)) is None:
        logging.warning(f"The user {user.db_id} tried to set a timezone {address} that is not correct. Trying again")

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR)
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()
    # The end of the conversation is the only legitimate JOINED_ONLY/LEFT → MEMBER
    # transition site. Brand-new users are already MEMBER (model default), so this
    # is a no-op for them.
    user.status = UserStatus.MEMBER

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view(lang=user.lang).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR, value=0)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@with_async_session
async def registration_timezone_location_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    logging.debug("Enter into registration_timezone_location_message_handler")
    context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION)

    user = guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)) is None:
        logging.warning(f"The user {user.db_id} tried to set a location {location} that is not correct. Trying again")

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR, value=1)
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    session.flush()
    # The end of the conversation is the only legitimate JOINED_ONLY/LEFT → MEMBER
    # transition site. Brand-new users are already MEMBER (model default), so this
    # is a no-op for them.
    user.status = UserStatus.MEMBER

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(timezone=user.settings.timezone)
    view = factory.main_menu_view(lang=user.lang).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR, value=0)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_INVALID_INPUT,
    ~filters.TEXT | filters.COMMAND,
    bindable=False,
)
@with_async_session
async def registration_timezone_invalid_input_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState:
    user = guards.current_user(update, session)
    await context.api.send_message(
        update=update,
        view=RegistrationMessages.TIMEZONE_INVALID_INPUT.get(lang=user.lang),
    )
    return ConversationRegistrationProcessState.TIMEZONE


HandlersRegistry.register_conversation_handler(
    RegistrationProcessHandlerId.TIMEZONE_CONVERSATION,
    entry_points_handler_names=[RegistrationProcessHandlerId.TIMEZONE_COMMAND],
    states={
        ConversationRegistrationProcessState.TIMEZONE: [
            RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
            RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
        ],
    },
    fallbacks=[RegistrationProcessHandlerId.TIMEZONE_INVALID_INPUT],
)
