from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(MainMenuHandlerId.SHOW_HELP_CALLBACK, callback_data=cb.HELP, bindable=True)
@with_session
async def callback_query_help(session: AsyncSession, update: Update, context: TMitupContext):
    # The help screen renders only `user.lang`; it never traverses the meetups/joined_links
    # collections, so skip loading them.
    user = await guards.current_user(update, session, load_collections=False)
    view = views.factory.help_view(guards.render_context(user, update, context))

    await context.api.edit_message(update=update, view=view)
