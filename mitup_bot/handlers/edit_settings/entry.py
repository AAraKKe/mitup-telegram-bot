import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import api, guards, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import callbacks as cb

from .enums import EditSettingsHandlerId


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.EDIT, callback_data=cb.SETTINGS, bindable=True)
@with_async_session
async def callback_query_settings(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_settings")

    user = guards.current_user(update, session)
    view = views.factory.settings_view(lang=user.lang)

    await api.edit_message(context=context, update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.CANCEL, callback_data=cb.CANCEL_SETTINGS, bindable=False
)
@with_async_session
async def callback_query_cancel_settings(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_cancel_settings")

    user = guards.current_user(update, session)
    view = views.factory.settings_view(lang=user.lang)

    await api.send_message(context=context, update=update, view=view)

    return ConversationHandler.END
