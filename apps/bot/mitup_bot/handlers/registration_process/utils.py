import functools
from collections.abc import Callable, Coroutine
from typing import Any, NoReturn

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from mitup_bot import docs_links, guards
from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.timezone_api import TimezoneInputMethod, TimezoneLookupFailure
from mitup_bot.translations import locale_for_language_code
from mitup_bot.utils.entities import Link, render
from mitup_bot.utils.messages import RegistrationMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import MitupView

from .enums import ConversationRegistrationProcessState

log = structlog.get_logger(__name__)

TIMEZONE_PROMPT_STEP = "timezone_prompt"


def claim_update[**P](
    func: Callable[P, Coroutine[Any, Any, object]],
) -> Callable[P, Coroutine[Any, Any, NoReturn]]:
    """Re-raise the handler's returned state as ApplicationHandlerStop.

    The registration conversation binds in REGISTRATION_HANDLERS_GROUP (before group 0), so its
    handlers must claim the updates they process or group-0 handlers would process them a second
    time. PTB applies the state carried by the exception and stops all later handler groups.

    Must wrap OUTSIDE `with_session` (i.e. closer to the registration decorator): raising inside
    the session scope would roll back the transaction.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> NoReturn:
        next_state = await func(*args, **kwargs)
        log.info("Onboarding update claimed", next_state=str(next_state), reason="registration_group_precedence")
        raise ApplicationHandlerStop(next_state)

    return wrapper


async def get_or_create_onboarding_user(session: AsyncSession, update: Update) -> tuple[User, bool]:
    """Return the `User` for the update and whether this call created it.

    The /start re-onboarding flow must serve three cases under one entry
    point: brand-new (no row), JOINED_ONLY (inline-joined, never DM-ed), and
    LEFT (re-onboarding). For the existing-row cases we reuse the row and its
    attached Settings — the timezone conversation will then reset state and
    promote the user to MEMBER on success. The caller cannot tell the three
    apart afterwards, so the fork is named on the way out.
    """
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    if (existing_user := await User.by_tg_user_id(session, update.effective_user.id)) is not None:
        log.info(
            "Onboarding user resolved",
            user_id=existing_user.db_id,
            status=existing_user.status.value,
            language=existing_user.lang,
            reason="existing_row",
        )
        return existing_user, False

    new_user = User(
        first_name=update.effective_user.first_name,
        tg_user_id=update.effective_user.id,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        settings=Settings(language=locale_for_language_code(update.effective_user.language_code)),
    )
    session.add(new_user)
    # Flushed here so the row's id exists for the rest of the funnel: every later line names the
    # user by `user_id`, and the insert would otherwise only land at the end of the handler.
    await session.flush()
    log.info(
        "Onboarding user created",
        user_id=new_user.db_id,
        status=new_user.status.value,
        language=new_user.lang,
        reason="no_existing_row",
    )
    return new_user, True


def registration_complete_view(user: User, update: Update, context: TMitupContext) -> MitupView:
    """Main-menu view whose description is the registration-complete welcome.

    One cohesive message: the welcome, the timezone confirmation, an inline link to the user
    guide, and the Collaborate pointer, with the main-menu keyboard (Collaborate included)
    directly below.
    """
    user_guide_link = render(
        t"{Link(RegistrationMessages.USER_GUIDE_LABEL.get_text(lang=user.lang), docs_links.user_guide_url())}"
    )
    message = RegistrationMessages.REGISTRATION_COMPLETE.get(
        timezone=user.settings.timezone, user_guide=user_guide_link, lang=user.lang
    )
    return factory.main_menu_view(guards.render_context(user, update, context), message=message)


def promote_onboarded_user(user: User):
    """Move the user to MEMBER, the only legitimate JOINED_ONLY/LEFT → MEMBER transition site.

    Brand-new users are already MEMBER (model default), so this is a no-op for them and the
    transition line is written only for a status that really changed.
    """
    prior_status = user.status
    user.set_status(UserStatus.MEMBER)
    if prior_status is UserStatus.MEMBER:
        return

    log.info(
        "User promoted to member",
        user_id=user.db_id,
        prior_status=prior_status.value,
        reason="onboarding_completed",
    )


async def complete_onboarding(
    session: AsyncSession,
    user: User,
    timezone: str,
    input_method: TimezoneInputMethod,
    update: Update,
    context: TMitupContext,
) -> int:
    """Store the onboarding timezone, promote the user, and answer with the welcome screen."""
    old_timezone = user.settings.timezone
    user.settings.timezone = timezone

    session.add(user)
    await session.flush()
    log.info(
        "User timezone set",
        user_id=user.db_id,
        old_timezone=old_timezone,
        new_timezone=timezone,
        source="onboarding",
        input_method=input_method.value,
    )

    promote_onboarded_user(user)

    await context.api.send_message(update=update, view=registration_complete_view(user, update, context))
    log.info(
        "Onboarding completed",
        user_id=user.db_id,
        input_method=input_method.value,
        timezone=timezone,
        lang=user.lang,
    )

    context.put_feature_metric(Feature.NEW_USER_REGISTERED)
    context.put_feature_metric(
        Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=0, properties={"InputMethod": input_method.value}
    )
    return ConversationHandler.END


async def retry_timezone_step(
    user: User,
    failure: TimezoneLookupFailure,
    input_method: TimezoneInputMethod,
    update: Update,
    context: TMitupContext,
    **input_facts: object,
) -> ConversationRegistrationProcessState:
    """Re-prompt for the timezone, recording why the answer the user gave resolved to none."""
    log.warning(
        "Onboarding step retried",
        user_id=user.db_id,
        step=TIMEZONE_PROMPT_STEP,
        input_method=input_method.value,
        reason=failure.value,
        **input_facts,
    )

    await context.api.send_message(update=update, view=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang))

    context.put_feature_metric(
        Feature.SET_TIMEZONE, name=MetricKey.ERROR, value=1, properties={"InputMethod": input_method.value}
    )
    return ConversationRegistrationProcessState.TIMEZONE
