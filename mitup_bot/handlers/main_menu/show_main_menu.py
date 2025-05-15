import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import api, guards, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import callbacks as cb

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.MAIN_MENU_CALLBACK, callback_data=cb.MAIN_MENU, bindable=True
)
@with_async_session
async def callback_query_main_menu(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_main_menu")

    context.clean_all_user_data()

    user = guards.current_user(update, session)
    view = views.factory.main_menu_view(lang=user.lang)

    await api.edit_message(context=context, update=update, view=view)
