from sqlmodel import Session
from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.models import Settings, User


def get_or_create_onboarding_user(session: Session, update: Update) -> User:
    """Return the existing `User` for the update, or create a brand-new one.

    The /start re-onboarding flow must serve three cases under one entry
    point: brand-new (no row), JOINED_ONLY (inline-joined, never DM-ed), and
    LEFT (re-onboarding). For the existing-row cases we reuse the row and its
    attached Settings — the timezone conversation will then reset state and
    promote the user to MEMBER on success.
    """
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    if (existing_user := User.by_tg_user_id(session, update.effective_user.id)) is not None:
        return existing_user

    new_user = User(
        first_name=update.effective_user.first_name,
        tg_user_id=update.effective_user.id,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        settings=Settings(),
    )
    session.add(new_user)
    return new_user
