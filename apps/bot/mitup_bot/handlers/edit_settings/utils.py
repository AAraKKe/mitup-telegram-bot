from collections.abc import Sequence

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import User

from .enums import SettingName

log = structlog.get_logger(__name__)

# One event name for every settings mutation in the package, with `setting` as the filterable
# facet, so a single query returns a user's whole change history.
SETTING_CHANGED_EVENT = "User setting changed"


async def set_settings(session: AsyncSession, user: User, settings: Sequence[SettingName], *, value: bool):
    """Write one value across several boolean settings, recording only the ones that change."""
    changed = [setting for setting in settings if getattr(user.settings, setting.value) != value]

    for setting in changed:
        setattr(user.settings, setting.value, value)
    await session.flush()

    for setting in changed:
        log.info(
            SETTING_CHANGED_EVENT,
            user_id=user.db_id,
            setting=setting.value,
            old_value=not value,
            new_value=value,
        )


async def toggle_setting(session: AsyncSession, user: User, setting: SettingName) -> bool:
    """Flip one boolean setting, returning its new value."""
    new_value = not getattr(user.settings, setting.value)
    await set_settings(session, user, [setting], value=new_value)
    return new_value
