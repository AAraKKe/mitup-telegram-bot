import logging

from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import api, views
from mitup_bot.custom_context import MitupContext
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import callbacks as cb

from .enums import EditSettingsHandlerId


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.EDIT, callback_data=cb.SETTINGS, bindable=True)
async def callback_query_settings(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_settings")

    view = views.factory.settings_view()

    await api.edit_message(context, update, view)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.CANCEL, callback_data=cb.CANCEL_SETTINGS, bindable=False
)
async def callback_query_cancel_settings(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_cancel_settings")

    view = views.factory.settings_view()

    await api.send_message(context, update, view)

    return ConversationHandler.END
