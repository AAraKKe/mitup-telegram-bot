from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet

from . import Settings, User


def user_from_update(update: Update) -> User:
    """Given an update with an effective user, return a new User instance with properties from the effective user."""
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    return User(
        tg_user_id=update.effective_user.id,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        settings=Settings(),
    )
