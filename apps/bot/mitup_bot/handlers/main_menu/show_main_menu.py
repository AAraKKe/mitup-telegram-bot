from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.utils import Screen, ScreenDelivery, log_screen_shown
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.MAIN_MENU_CALLBACK, callback_data=cb.MAIN_MENU, bindable=True
)
@with_session
async def callback_query_main_menu(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data(reason="main_menu_navigation")

    # The main menu renders only `user.lang` and the admin flag; it never traverses the
    # meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session)
    view = views.factory.main_menu_view(guards.render_context(user, update, context))

    log_screen_shown(user, Screen.MAIN_MENU, ScreenDelivery.EDIT)
    await context.api.edit_message(update=update, view=view)
