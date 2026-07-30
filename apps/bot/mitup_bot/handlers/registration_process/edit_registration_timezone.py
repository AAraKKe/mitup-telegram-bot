from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Location, MessageEntity, Update
from telegram.ext import ApplicationHandlerStop, ConversationHandler, filters

from mitup_bot import guards, timezone_api
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.start_payload import start_acquisition_source
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.timezone_api import TimezoneInputMethod, TimezoneLookupFailure
from mitup_bot.utils import PrivacyMessages, RegistrationMessages

from .enums import REGISTRATION_HANDLERS_GROUP, ConversationRegistrationProcessState, RegistrationProcessHandlerId
from .utils import (
    TIMEZONE_PROMPT_STEP,
    claim_update,
    complete_onboarding,
    get_or_create_onboarding_user,
    retry_timezone_step,
)

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
    if (member := await guards.member_user(update, session)) is not None:
        log.info("Onboarding entry declined", user_id=member.db_id, reason="already_member")
        return None

    user, is_new_user = await get_or_create_onboarding_user(session, update, start_acquisition_source(context.args))
    if user.status is UserStatus.DELETION_REQUESTED:
        log.info("Onboarding refused", user_id=user.db_id, status=user.status.value, reason="deletion_requested")
        await context.api.send_message(update=update, view=PrivacyMessages.PENDING_DELETION_ALERT.get(lang=user.lang))
        return ConversationHandler.END

    message = RegistrationMessages.TIMEZONE_PROMPT.get(first_name=user.first_name, lang=user.lang)

    # Logged before the send so a prompt that never reaches the user still counts as attempted.
    log.info(
        "Onboarding step shown",
        user_id=user.db_id,
        step=TIMEZONE_PROMPT_STEP,
        prior_status=user.status.value,
        is_new_user=is_new_user,
        lang=user.lang,
    )
    await context.api.send_message(update=update, view=message)

    return ConversationRegistrationProcessState.TIMEZONE


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT & ~filters.COMMAND, bindable=False
)
@claim_update
@with_session
async def registration_timezone_text_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.SET_TIMEZONE, properties={"InputMethod": TimezoneInputMethod.MESSAGE.value})

    # Re-onboarding writes `user.settings.timezone`/`user.status` and reads `user.lang`; it never
    # traverses the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    timezone = await timezone_api.get_timezone_by_address(address, context)
    if isinstance(timezone, TimezoneLookupFailure):
        return await retry_timezone_step(
            user, timezone, TimezoneInputMethod.MESSAGE, update, context, address_length=len(address)
        )

    return await complete_onboarding(session, user, timezone, TimezoneInputMethod.MESSAGE, update, context)


@HandlersRegistry.register_message(
    RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@claim_update
@with_session
async def registration_timezone_location_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationRegistrationProcessState | int:
    context.put_feature_metric(Feature.SET_TIMEZONE, properties={"InputMethod": TimezoneInputMethod.LOCATION.value})

    # Re-onboarding writes `user.settings.timezone`/`user.status` and reads `user.lang`; it never
    # traverses the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    timezone = await timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)
    if isinstance(timezone, TimezoneLookupFailure):
        # No coordinates on this line: two decimals still resolves to about a kilometre, which
        # locates a home. The Google call's own line carries them under the same update_id.
        return await retry_timezone_step(user, timezone, TimezoneInputMethod.LOCATION, update, context)

    return await complete_onboarding(session, user, timezone, TimezoneInputMethod.LOCATION, update, context)


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
    user = await guards.current_user(update, session)
    message = guards.message(update)
    log.warning(
        "Onboarding step rejected input",
        user_id=user.db_id,
        step=TIMEZONE_PROMPT_STEP,
        reason="unsupported_input_kind",
        is_command=any(entity.type == MessageEntity.BOT_COMMAND and entity.offset == 0 for entity in message.entities),
    )
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
