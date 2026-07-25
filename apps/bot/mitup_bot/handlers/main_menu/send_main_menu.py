from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SEND_MAIN_MENU_CALLBACK, callback_data=cb.SEND_MAIN_MENU, bindable=True
)
@with_session
async def callback_query_send_main_menu(session: AsyncSession, update: Update, context: TMitupContext):
    # Navigation-only entry point for standalone messages (e.g. broadcasts): send the main menu as a
    # NEW message so the tapped message stays in the chat. Unlike MAIN_MENU this neither edits nor
    # clears user data, so it never disturbs an unrelated in-progress conversation.

    # The main menu renders only `user.lang` and the admin flag; it never traverses the
    # meetups/joined_links collections, so skip loading them.
    user = await guards.current_user(update, session)
    view = views.factory.main_menu_view(guards.render_context(user, update, context))

    await context.api.send_message(update=update, view=view)
