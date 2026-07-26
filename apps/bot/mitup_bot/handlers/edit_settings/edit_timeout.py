from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, limits, views
from mitup_bot.db import with_session
from mitup_bot.handlers import BoundedPositiveNumberFilter, HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages

from .enums import ConversationSettingsState, EditSettingsHandlerId


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TIMEOUT_CALLBACK, callback_data=cb.EDIT_TIMEOUT, bindable=False
)
@with_session
async def callback_query_timeout(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: reads `user.lang`/`user.settings`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    message = SettingsMessages.TIMEOUT_PROMPT.get(
        lang=user.lang, timeout=user.settings.timeout, max_timeout=limits.MEETING_MAX_TIMEOUT_MINUTES
    )

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.edit_message(update=update, view=view)

    return ConversationSettingsState.TIMEOUT


@HandlersRegistry.register_message(
    EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT,
    BoundedPositiveNumberFilter(limits.MEETING_MAX_TIMEOUT_MINUTES),
    bindable=False,
)
@with_session
async def settings_timeout_text_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    timeout_str = cast(str, guards.message(update).text)

    timeout = int(timeout_str)

    user.settings.timeout = timeout
    await session.flush()

    message = SettingsMessages.TIMEOUT_SUCCESS.get(lang=user.lang, timeout=user.settings.timeout)
    view = views.factory.settings_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(EditSettingsHandlerId.TIMEOUT_INVALID_INPUT, filters=filters.ALL, bindable=False)
@with_session
async def settings_timeout_invalid_input_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    message = SettingsMessages.TIMEOUT_INVALID.get(lang=user.lang, max_timeout=limits.MEETING_MAX_TIMEOUT_MINUTES)

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

    return ConversationSettingsState.TIMEOUT


HandlersRegistry.register_conversation_handler(
    EditSettingsHandlerId.TIMEOUT_CONVERSATION,
    entry_points_handler_names=[EditSettingsHandlerId.TIMEOUT_CALLBACK],
    states={
        ConversationSettingsState.TIMEOUT: [
            EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT,
            EditSettingsHandlerId.CANCEL,
        ],
    },
    fallbacks=[EditSettingsHandlerId.TIMEOUT_INVALID_INPUT],
)
