from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import AdminHandlerId


@HandlersRegistry.register_callback_query(
    AdminHandlerId.ADMIN_MENU_CALLBACK, callback_data=cb.ADMIN_MENU, admin_only=True
)
@with_session
async def callback_query_show_admin_menu(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data()

    # Admin menu reads only `user.lang`; it never traverses the meetups/joined_links collections.
    user = await guards.current_user(update, session, load_collections=False)
    view = views.factory.admin_menu_view(guards.render_context(user, update, context))

    await context.api.edit_message(update=update, view=view)
