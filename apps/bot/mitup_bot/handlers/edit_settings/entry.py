import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import Screen, ScreenDelivery, log_screen_shown
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import EditSettingsHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.EDIT, callback_data=cb.SETTINGS, bindable=True)
@with_session
async def callback_query_settings(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only screen: every handler in this package reads `user.lang`/`user.settings` and
    # never the meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session)
    view = views.factory.settings_view(guards.render_context(user, update, context))

    log_screen_shown(user, Screen.SETTINGS, ScreenDelivery.EDIT)
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.CANCEL, callback_data=cb.CANCEL_SETTINGS, bindable=False
)
@with_session
async def callback_query_cancel_settings(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    view = views.factory.settings_view(guards.render_context(user, update, context))

    log.info("Settings conversation cancelled", user_id=user.db_id, reason="user_cancelled")
    await context.api.edit_message(update=update, view=view)

    return ConversationHandler.END
