import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import User

from .enums import SettingName

log = structlog.get_logger(__name__)

# One event name for every settings mutation in the package, with `setting` as the filterable
# facet, so a single query returns a user's whole change history.
SETTING_CHANGED_EVENT = "User setting changed"
SETTINGS_MENU_SOURCE = "settings_menu"
DEFAULT_OPTIONS_SOURCE = "default_meeting_options"


async def toggle_default_meeting_option(session: AsyncSession, user: User, setting: SettingName) -> bool:
    """Flip one default-meeting-option flag, returning its new value.

    Each flag is copied into every meeting the user creates afterwards, so the flip needs a change
    history; routing all five through here keeps that to one call site.
    """
    old_value: bool = getattr(user.settings, setting.value)
    new_value = not old_value

    setattr(user.settings, setting.value, new_value)
    await session.flush()

    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=setting.value,
        old_value=old_value,
        new_value=new_value,
        source=DEFAULT_OPTIONS_SOURCE,
    )
    return new_value
