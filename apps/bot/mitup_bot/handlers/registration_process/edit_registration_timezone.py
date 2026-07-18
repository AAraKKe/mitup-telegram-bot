from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Location, Update
from telegram.ext import ApplicationHandlerStop, ConversationHandler, filters

from mitup_bot import guards, timezone_api
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import PrivacyMessages, RegistrationMessages
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
) -> ConversationRegistrationProcessState | int | None:
    """Prompt for a timezone and enter the conversation, or None when the user is a MEMBER.

    A user marked for deletion falls through `guards.member_user` (not a MEMBER) into this entry,
    so the no-undo check lives here: their row must not be reused or promoted, they get the
    pending-deletion notice, and the update is claimed with END so group 0 never sees it.
    """
    if await guards.member_user(update, session) is not None:
        return None

    user = await get_or_create_onboarding_user(session, update)
    if user.status is UserStatus.DELETION_REQUESTED:
        await context.api.send_message(update=update, view=PrivacyMessages.PENDING_DELETION_ALERT.get(lang=user.lang))
        return ConversationHandler.END

    message = RegistrationMessages.TIMEZONE_PROMPT.get(first_name=user.first_name, lang=user.lang)

    await context.api.send_message(update=update, view=message)

    log.info("Onboarding landing shown")
    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT & ~filters.COMMAND, bindable=False
)
@claim_update
@with_session
async def registration_timezone_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.SET_TIMEZONE, properties={"InputMethod": "message"})

    # Re-onboarding writes `user.settings.timezone`/`user.status` and reads `user.lang`; it never
    # traverses the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session, load_collections=False)
    address = cast(str, guards.message(update).text)

    if (new_timezone := timezone_api.get_timezone_by_address(address, context)) is None:
        log.warning("User provided an invalid timezone, retrying", user_id=user.db_id)

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(
            Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=1, properties={"InputMethod": "message"}
        )
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    await session.flush()
    # The end of the conversation is the only legitimate JOINED_ONLY/LEFT → MEMBER
    # transition site. Brand-new users are already MEMBER (model default), so this
    # is a no-op for them.
    user.status = UserStatus.MEMBER

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(timezone=user.settings.timezone, lang=user.lang)
    view = factory.main_menu_view(guards.render_context(user, update, context)).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(
        Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=0, properties={"InputMethod": "message"}
    )
    return ConversationHandler.END


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@claim_update
@with_session
async def registration_timezone_location_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.SET_TIMEZONE, properties={"InputMethod": "location"})

    # Re-onboarding writes `user.settings.timezone`/`user.status` and reads `user.lang`; it never
    # traverses the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session, load_collections=False)
    location = cast(Location, guards.message(update).location)

    if (new_timezone := timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)) is None:
        log.warning("User provided an invalid location, retrying", user_id=user.db_id)

        await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

        context.put_feature_metric(
            Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=1, properties={"InputMethod": "location"}
        )
        return ConversationRegistrationProcessState.TIMEZONE

    user.settings.timezone = new_timezone

    session.add(user)
    await session.flush()
    # The end of the conversation is the only legitimate JOINED_ONLY/LEFT → MEMBER
    # transition site. Brand-new users are already MEMBER (model default), so this
    # is a no-op for them.
    user.status = UserStatus.MEMBER

    message = RegistrationMessages.TIMEZONE_SUCCESS.get(timezone=user.settings.timezone, lang=user.lang)
    view = factory.main_menu_view(guards.render_context(user, update, context)).with_context(message)

    await context.api.send_message(update=update, view=view)

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(
        Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=0, properties={"InputMethod": "location"}
    )
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
    # Reads only `user.lang`; never traverses the meetups/joined_links collections.
    user = await guards.current_user(update, session, load_collections=False)
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
