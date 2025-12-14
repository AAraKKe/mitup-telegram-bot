from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.views import factory

from .registry import HandlersRegistry


class MessagesId(HandlerId):
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_WITHOUT_TEXT, ~filters.TEXT | filters.COMMAND, bindable=False)
@with_async_session
async def filter_messages_without_text(session: Session, update: Update, context: MitupContext):
    context.clean_all_user_data()

    user = guards.current_user(update, session)
    view = factory.main_menu_view(lang=user.lang)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END
