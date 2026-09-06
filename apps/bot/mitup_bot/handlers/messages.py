from enum import auto

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.views import factory

from .personal_filters import RichMessageFilter
from .registry import HandlersRegistry
from .utils import reply_rich_message_not_supported


class MessagesId(HandlerId):
    MESSAGE_CREATE_MEETING = auto()
    MESSAGE_WITHOUT_TEXT = auto()
    MESSAGE_RICH = auto()


@HandlersRegistry.register_message(MessagesId.MESSAGE_WITHOUT_TEXT, ~filters.TEXT | filters.COMMAND, bindable=False)
@with_session
async def filter_messages_without_text(session: AsyncSession, update: Update, context: TMitupContext):
    # Neither view traverses the meetups/joined_links collections: the menu sizes its list chips
    # with counting queries instead.
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)

    if on_exit := context.get_active_on_exit():
        view = factory.conversation_interrupted_view(
            ctx.with_lang(on_exit.lang), notice=on_exit.notice, cancel_callback=on_exit.cancel_callback
        )
        await context.api.send_message(update=update, view=view)
        return None

    context.clean_all_user_data(reason="conversation_abandoned")
    view = factory.main_menu_view(ctx, counts=await user.meeting_counts(session))
    await context.api.send_message(update=update, view=view)
    return ConversationHandler.END


@HandlersRegistry.register_message(MessagesId.MESSAGE_RICH, RichMessageFilter())
@with_session
async def rich_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    # The idle rich-message path: each conversation binds its own rich fallback and conversation
    # handlers sort first in the group, so this global handler fires only when no conversation is
    # active. Reply with the not-supported notice on top of the main menu so the user is never
    # stranded, and clear any leftover user data.
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)
    context.clean_all_user_data(reason="idle_rich_message")
    await reply_rich_message_not_supported(
        ctx, update, context, factory.main_menu_view(ctx, counts=await user.meeting_counts(session))
    )
    return None
