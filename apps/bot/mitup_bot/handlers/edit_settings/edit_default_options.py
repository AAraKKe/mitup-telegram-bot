import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.datetimes import DateFormat
from mitup_bot.db import with_session
from mitup_bot.guards import current_user
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.views.meeting_settings import (
    default_behavior_view,
    default_meeting_settings_view,
    default_time_format_view,
)

from .entry import EditSettingsHandlerId
from .enums import SettingName
from .utils import DEFAULT_OPTIONS_SOURCE, SETTING_CHANGED_EVENT, toggle_default_meeting_option

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.DEFAULT_OPTIONS_CALLBACK, callback_data=cb.EDIT_DEFAULT_OPTIONS
)
@with_session
async def callback_query_edit_default_meeting_options(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: `default_meeting_settings_view` renders `user.settings` and never the
    # meetups/joined_links collections, so skip loading them.
    user = await current_user(update, session)

    await context.api.edit_message(
        update=update,
        view=default_meeting_settings_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.OPEN_DEFAULT_BEHAVIOR, callback_data=cb.OPEN_DEFAULT_BEHAVIOR
)
@with_session
async def callback_query_open_default_behavior(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.OPEN_DEFAULT_TIME_FORMAT, callback_data=cb.OPEN_DEFAULT_TIME_FORMAT
)
@with_session
async def callback_query_open_default_time_format(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)

    await context.api.edit_message(
        update=update,
        view=default_time_format_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_WAITING_LIST, callback_data=cb.SET_DEFAULT_WAITING_LIST
)
@with_session
async def callback_query_toggle_default_waiting_list(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_WAITING_LIST)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.SET_DEFAULT_PUBLIC, callback_data=cb.SET_DEFAULT_PUBLIC)
@with_session
async def callback_query_toggle_default_public(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_PUBLIC)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INVITATIONS, callback_data=cb.SET_DEFAULT_INVITATIONS
)
@with_session
async def callback_query_toggle_default_invitations(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_ALLOW_INVITATION)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_INCOGNITO, callback_data=cb.SET_DEFAULT_INCOGNITO
)
@with_session
async def callback_query_toggle_default_incognito(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_INCOGNITO)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_LOCK_ON_START, callback_data=cb.SET_DEFAULT_LOCK_ON_START
)
@with_session
async def callback_query_toggle_default_lock_on_start(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_LOCK_ON_START)

    await context.api.edit_message(
        update=update,
        view=default_behavior_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_SHOW_TIMEZONE, callback_data=cb.SET_DEFAULT_SHOW_TIMEZONE
)
@with_session
async def callback_query_toggle_default_show_timezone(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_SHOW_TIMEZONE)

    await context.api.edit_message(
        update=update,
        view=default_time_format_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_CLOCK_24H, callback_data=cb.SET_DEFAULT_CLOCK_24H
)
@with_session
async def callback_query_toggle_default_clock_24h(session: AsyncSession, update: Update, context: TMitupContext):
    user = await current_user(update, session)
    await toggle_default_meeting_option(session, user, SettingName.DEFAULT_CLOCK_24H)

    await context.api.edit_message(
        update=update,
        view=default_time_format_view(user.settings),
    )


@HandlersRegistry.register_callback_query(
    EditSettingsHandlerId.SET_DEFAULT_DATE_FORMAT, callback_data=cb.SET_DEFAULT_DATE_FORMAT
)
@with_session
async def callback_query_set_default_date_format(session: AsyncSession, update: Update, context: TMitupContext):
    valid_data = guards.valid_callback_data(
        cb.SET_DEFAULT_DATE_FORMAT.parse(context.match), EditSettingsHandlerId.SET_DEFAULT_DATE_FORMAT
    )
    user = await current_user(update, session)
    settings = user.settings

    old_format = settings.default_date_format
    settings.default_date_format = list(DateFormat)[valid_data.id]
    await session.flush()
    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=SettingName.DEFAULT_DATE_FORMAT.value,
        old_value=old_format.value,
        new_value=settings.default_date_format.value,
        source=DEFAULT_OPTIONS_SOURCE,
    )

    await context.api.edit_message(
        update=update,
        view=default_time_format_view(settings),
    )
