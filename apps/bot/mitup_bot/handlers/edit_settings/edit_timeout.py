from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers import BoundedPositiveNumberFilter, HandlersRegistry
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages

from .enums import ConversationSettingsState, EditSettingsHandlerId, SettingName
from .utils import SETTING_CHANGED_EVENT, SETTINGS_MENU_SOURCE

log = structlog.get_logger(__name__)


def timeout_rejection_reason(text: str | None, max_timeout: int) -> str:
    """Name why `BoundedPositiveNumberFilter` refused an answer.

    A run of these separates a prompt nobody understands from users routinely asking for a longer
    lifetime than their tier allows — two different fixes behind one re-prompt.
    """
    answer = (text or "").strip()
    if answer.isdecimal() and int(answer) > max_timeout:
        return "timeout_above_maximum"
    return "not_a_positive_integer"


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TIMEOUT_CALLBACK, callback_data=cb.EDIT_TIMEOUT, bindable=False
)
@with_session
async def callback_query_timeout(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: reads `user.lang`/`user.settings`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    message = SettingsMessages.TIMEOUT_PROMPT.get(
        lang=user.lang, timeout=user.settings.timeout, max_timeout=LifecyclePolicy.get().max_timeout_minutes
    )

    log.info(
        "Settings step shown",
        user_id=user.db_id,
        setting=SettingName.TIMEOUT.value,
        current_value=user.settings.timeout,
    )

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.edit_message(update=update, view=view)

    return ConversationSettingsState.TIMEOUT


@HandlersRegistry.register_message(
    EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT,
    BoundedPositiveNumberFilter(LifecyclePolicy.get().max_timeout_minutes),
    bindable=False,
)
@with_session
async def settings_timeout_text_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    timeout_str = cast(str, guards.message(update).text)

    timeout = int(timeout_str)

    old_timeout = user.settings.timeout
    user.settings.timeout = timeout
    await session.flush()

    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=SettingName.TIMEOUT.value,
        old_value=old_timeout,
        new_value=timeout,
        source=SETTINGS_MENU_SOURCE,
    )

    message = SettingsMessages.TIMEOUT_SUCCESS.get(lang=user.lang, timeout=user.settings.timeout)
    view = views.factory.settings_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(EditSettingsHandlerId.TIMEOUT_INVALID_INPUT, filters=filters.ALL, bindable=False)
@with_session
async def settings_timeout_invalid_input_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    max_timeout = LifecyclePolicy.get().max_timeout_minutes
    log.warning(
        "Settings step rejected input",
        user_id=user.db_id,
        setting=SettingName.TIMEOUT.value,
        max_timeout=max_timeout,
        reason=timeout_rejection_reason(guards.message(update).text, max_timeout),
    )
    message = SettingsMessages.TIMEOUT_INVALID.get(lang=user.lang, max_timeout=max_timeout)

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
