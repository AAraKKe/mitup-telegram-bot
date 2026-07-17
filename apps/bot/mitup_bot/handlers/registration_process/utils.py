import functools
from collections.abc import Callable, Coroutine
from typing import Any, NoReturn

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ApplicationHandlerStop

from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.models import Settings, User
from mitup_bot.translations import locale_for_language_code


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
        raise ApplicationHandlerStop(await func(*args, **kwargs))

    return wrapper


async def get_or_create_onboarding_user(session: AsyncSession, update: Update) -> User:
    """Return the existing `User` for the update, or create a brand-new one.

    The /start re-onboarding flow must serve three cases under one entry
    point: brand-new (no row), JOINED_ONLY (inline-joined, never DM-ed), and
    LEFT (re-onboarding). For the existing-row cases we reuse the row and its
    attached Settings — the timezone conversation will then reset state and
    promote the user to MEMBER on success.
    """
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    if (existing_user := await User.by_tg_user_id(session, update.effective_user.id)) is not None:
        return existing_user

    new_user = User(
        first_name=update.effective_user.first_name,
        tg_user_id=update.effective_user.id,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        settings=Settings(language=locale_for_language_code(update.effective_user.language_code)),
    )
    session.add(new_user)
    return new_user
