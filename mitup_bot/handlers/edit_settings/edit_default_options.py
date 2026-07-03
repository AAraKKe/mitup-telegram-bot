from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.guards import current_user
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .entry import EditSettingsHandlerId


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.DEFAULT_OPTIONS_CALLBACK, callback_data=cb.EDIT_DEFAULT_OPTIONS
)
@with_session
async def callback_query_edit_default_meeting_options(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    user = await current_user(update, session)

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_WAITING_LIST, callback_data=cb.SET_DEFAULT_WAITING_LIST
)
@with_session
async def callback_query_toggle_default_waiting_list(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    user = await current_user(update, session)
    user.settings.default_waiting_list = not user.settings.default_waiting_list
    await session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.SET_DEFAULT_PUBLIC, callback_data=cb.SET_DEFAULT_PUBLIC)
@with_session
async def callback_query_toggle_default_public(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    user = await current_user(update, session)
    user.settings.default_public = not user.settings.default_public
    await session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INVITATIONS, callback_data=cb.SET_DEFAULT_INVITATIONS
)
@with_session
async def callback_query_toggle_default_invitations(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    user = await current_user(update, session)
    user.settings.default_allow_invitation = not user.settings.default_allow_invitation
    await session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INCOGNITO, callback_data=cb.SET_DEFAULT_INCOGNITO
)
@with_session
async def callback_query_toggle_default_incognito(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    user = await current_user(update, session)
    user.settings.default_incognito = not user.settings.default_incognito
    await session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_LOCK_ON_START, callback_data=cb.SET_DEFAULT_LOCK_ON_START
)
@with_session
async def callback_query_toggle_default_lock_on_start(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    user = await current_user(update, session)
    user.settings.default_lock_on_start = not user.settings.default_lock_on_start
    await session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )
