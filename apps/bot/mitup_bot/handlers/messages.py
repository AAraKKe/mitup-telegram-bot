from enum import auto

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.views import factory

from .registry import HandlersRegistry


class MessagesId(HandlerId):
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_WITHOUT_TEXT, ~filters.TEXT | filters.COMMAND, bindable=False)
@with_session
async def filter_messages_without_text(session: AsyncSession, update: Update, context: TMitupContext):
    # Reads only `user.lang` for the interrupted/main-menu views; never traverses the
    # meetups/joined_links collections.
    user = await guards.current_user(update, session, load_collections=False)
    ctx = guards.render_context(user, update, context)

    if on_exit := context.get_active_on_exit():
        view = factory.conversation_interrupted_view(
            ctx,
            message=on_exit.message,
            cancel_callback=on_exit.cancel_callback,
        )
        await context.api.send_message(update=update, view=view)
        return None

    context.clean_all_user_data()
    view = factory.main_menu_view(ctx)
    await context.api.send_message(update=update, view=view)
    return ConversationHandler.END
