from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import EditSettingsHandlerId


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.EDIT, callback_data=cb.SETTINGS, bindable=True)
@with_session
async def callback_query_settings(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    view = views.factory.settings_view(lang=user.lang)

    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.CANCEL, callback_data=cb.CANCEL_SETTINGS, bindable=False
)
@with_session
async def callback_query_cancel_settings(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    view = views.factory.settings_view(lang=user.lang)

    await context.api.edit_message(update=update, view=view)

    return ConversationHandler.END
