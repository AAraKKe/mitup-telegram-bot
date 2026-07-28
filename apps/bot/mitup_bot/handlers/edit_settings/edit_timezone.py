from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Location, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, timezone_api, views
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import reply_rich_message_not_supported
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import ButtonMessages, RegistrationMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, factory

from .enums import ConversationSettingsState, EditSettingsHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.TIMEZONE_CALLBACK, callback_data=cb.EDIT_TIEMZONE, bindable=False
)
@with_session
async def callback_query_timezone(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: reads `user.lang`/`user.settings`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    message = SettingsMessages.TIMEZONE_PROMPT.get(lang=user.lang, timezone=user.settings.timezone)

    context.store_on_exit(
        ContextId.EDIT_SETTINGS_TIMEZONE,
        SettingsMessages.TIMEZONE_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_SETTINGS,
    )

    view = views.factory.change_settings_element_view(guards.render_context(user, update, context), message=message)

    await context.api.edit_message(update=update, view=view)

    return ConversationSettingsState.TIMEZONE


@HandlersRegistry.register_message(
    EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_TEXT, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_session
async def settings_timezone_text_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    address = cast(str, guards.message(update).text)

    if (new_timezone := await timezone_api.get_timezone_by_address(address, context)) is None:
        log.warning("User provided an invalid timezone, retrying", user_id=user.db_id)

        view = MitupView(
            description=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get_text(lang=user.lang),
                        callback_data=cb.CANCEL_SETTINGS,
                    )
                ]
            ],
        )
        await context.api.send_message(update=update, view=view)

        return ConversationSettingsState.TIMEZONE

    user.settings.timezone = new_timezone

    await session.flush()

    message = SettingsMessages.TIMEZONE_SUCCESS.get(lang=user.lang, timezone=user.settings.timezone)
    view = factory.settings_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION, filters.LOCATION, bindable=False
)
@with_session
async def settings_timezone_location_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    location = cast(Location, guards.message(update).location)

    if (
        new_timezone := await timezone_api.get_timezone_by_location(location.latitude, location.longitude, context)
    ) is None:
        log.warning("User provided an invalid location, retrying", user_id=user.db_id)

        view = MitupView(
            description=RegistrationMessages.TIMEZONE_FAIL.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get_text(lang=user.lang),
                        callback_data=cb.CANCEL_SETTINGS,
                    )
                ]
            ],
        )
        await context.api.send_message(update=update, view=view)

        return ConversationSettingsState.TIMEZONE

    user.settings.timezone = new_timezone

    await session.flush()

    message = SettingsMessages.TIMEZONE_SUCCESS.get(lang=user.lang, timezone=user.settings.timezone)
    view = factory.settings_view(guards.render_context(user, update, context), message=message)

    await context.api.send_message(update=update, view=view)

    return ConversationHandler.END


@HandlersRegistry.register_message(EditSettingsHandlerId.TIMEZONE_RICH_MESSAGE, RichMessageFilter(), bindable=False)
@with_session
async def settings_timezone_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationSettingsState:
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)
    message = SettingsMessages.TIMEZONE_PROMPT.get(lang=user.lang, timezone=user.settings.timezone)
    view = views.factory.change_settings_element_view(ctx, message=message)
    await reply_rich_message_not_supported(ctx, update, context, view)
    return ConversationSettingsState.TIMEZONE


HandlersRegistry.register_conversation_handler(
    EditSettingsHandlerId.TIMEZONE_CONVERSATION,
    entry_points_handler_names=[EditSettingsHandlerId.TIMEZONE_CALLBACK],
    states={
        ConversationSettingsState.TIMEZONE: [
            EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
            EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
            EditSettingsHandlerId.CANCEL,
        ],
    },
    fallbacks=[EditSettingsHandlerId.TIMEZONE_RICH_MESSAGE, MessagesId.MESSAGE_WITHOUT_TEXT],
)
