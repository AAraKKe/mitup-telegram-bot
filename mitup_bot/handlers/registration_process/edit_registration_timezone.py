from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Location, Update
from telegram.ext import ApplicationHandlerStop, ConversationHandler, filters

from mitup_bot import guards, timezone_api
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import RegistrationMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import REGISTRATION_HANDLERS_GROUP, ConversationRegistrationProcessState, RegistrationProcessHandlerId
from .utils import claim_update, get_or_create_onboarding_user

log = structlog.get_logger(__name__)


@HandlersRegistry.register_command(
    RegistrationProcessHandlerId.TIMEZONE_COMMAND,
    command="start",
    bindable=False,
)
async def command_start_with_new_user(
    update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    state = await start_onboarding(update, context)
    if state is None:
        # Already a MEMBER: their /start belongs to the group-0 handlers (main menu /
        # create-meeting deep link), so stay silent and let the update fall through.
        return ConversationHandler.END
    # Claim the update so the group-0 /start handler never sees it. Raised outside
    # start_onboarding so the session commits before the exception unwinds.
    raise ApplicationHandlerStop(state)


@with_session
async def start_onboarding(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | None:
    """Prompt for a timezone and enter the conversation, or None when the user is a MEMBER."""
    if await guards.member_user(update, session) is not None:
        return None

    user = await get_or_create_onboarding_user(session, update)
    message = RegistrationMessages.TIMEZONE_PROMPT.get(first_name=user.first_name)

    await context.api.send_message(update=update, view=message)

    context.put_feature_metric(Feature.NEW_LANDING)
    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT & ~filters.COMMAND, bindable=False
)
@claim_update
@with_session
async def registration_timezone_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE)

    user = await guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address, context)) is None:
        log.warning("User provided an invalid timezone, retrying", user_id=user.db_id)

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR)
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    await session.flush()
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
@claim_update
@with_session
async def registration_timezone_location_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION)

    user = await guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)) is None:
        log.warning("User provided an invalid location, retrying", user_id=user.db_id)

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR, value=1)
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    await session.flush()
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
@claim_update
@with_session
async def registration_timezone_invalid_input_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState:
    user = await guards.current_user(update, session)
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
    group=REGISTRATION_HANDLERS_GROUP,
)
