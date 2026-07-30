import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import AdminHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    AdminHandlerId.ADMIN_MENU_CALLBACK, callback_data=cb.ADMIN_MENU, admin_only=True
)
@with_session
async def callback_query_show_admin_menu(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data(reason="admin_menu_navigation")

    # Admin menu reads only `user.lang`; it never traverses the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    # Granted admin access, next to the registry's warning on a refused one: without this line the
    # log answers who was turned away and never who got in.
    log.info("Admin menu opened", user_id=user.db_id, stage="render")
    view = views.factory.admin_menu_view(guards.render_context(user, update, context))

    await context.api.edit_message(update=update, view=view)
