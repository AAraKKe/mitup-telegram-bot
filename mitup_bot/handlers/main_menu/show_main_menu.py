from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.MAIN_MENU_CALLBACK, callback_data=cb.MAIN_MENU, bindable=True
)
@with_session
async def callback_query_main_menu(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data()

    user = await guards.current_user(update, session)
    view = views.factory.main_menu_view(lang=user.lang)

    await context.api.edit_message(update=update, view=view)
