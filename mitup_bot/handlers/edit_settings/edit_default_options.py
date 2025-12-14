from sqlmodel import Session
from telegram import Update

from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.guards import current_user
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import callbacks as cb

from .entry import EditSettingsHandlerId


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.DEFAULT_OPTIONS_CALLBACK, callback_data=cb.EDIT_DEFAULT_OPTIONS
)
@with_async_session
async def callback_query_edit_default_meeting_options(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_WAITING_LIST, callback_data=cb.SET_DEFAULT_WAITING_LIST
)
@with_async_session
async def callback_query_toggle_default_waiting_list(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)
    user.settings.default_waiting_list = not user.settings.default_waiting_list
    session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.SET_DEFAULT_PUBLIC, callback_data=cb.SET_DEFAULT_PUBLIC)
@with_async_session
async def callback_query_toggle_default_public(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)
    user.settings.default_public = not user.settings.default_public
    session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INVITATIONS, callback_data=cb.SET_DEFAULT_INVITATIONS
)
@with_async_session
async def callback_query_toggle_default_invitations(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)
    user.settings.default_allow_invitation = not user.settings.default_allow_invitation
    session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INCOGNITO, callback_data=cb.SET_DEFAULT_INCOGNITO
)
@with_async_session
async def callback_query_toggle_default_incognito(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)
    user.settings.default_incognito = not user.settings.default_incognito
    session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_SHOW_TIMEZONE, callback_data=cb.SET_DEFAULT_SHOW_TIMEZONE
)
@with_async_session
async def callback_query_toggle_default_show_timezone(session: Session, update: Update, context: MitupContext):
    user = current_user(update, session)
    user.settings.default_show_timezone = not user.settings.default_show_timezone
    session.flush()

    await context.api.edit_message(
        update=update,
        view=user.settings.default_meeting_settings_view(),
    )
