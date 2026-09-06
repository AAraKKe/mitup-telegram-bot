from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.datetimes import DateFormat
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views

from .enums import EditMeetingHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK, callback_data=cb.EDIT_MEETING_SETTINGS
)
@with_session
async def callback_query_edit_meeting_settings(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_SETTINGS.parse(context.match), EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK
    ).id

    meeting = await guards.meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        action="edit_meeting_settings",
        context=context,
    )

    await context.api.edit_message(update=update, view=meeting_views.settings_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_MEETING_BEHAVIOR, callback_data=cb.OPEN_MEETING_BEHAVIOR
)
@with_session
async def callback_query_open_meeting_behavior(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(
        cb.OPEN_MEETING_BEHAVIOR.parse(context.match), EditMeetingHandlerId.OPEN_MEETING_BEHAVIOR
    ).id

    meeting = await guards.meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        action="open_meeting_behavior",
        context=context,
    )

    await context.api.edit_message(update=update, view=meeting_views.behavior_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.OPEN_MEETING_TIME_FORMAT, callback_data=cb.OPEN_MEETING_TIME_FORMAT
)
@with_session
async def callback_query_open_meeting_time_format(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(
        cb.OPEN_MEETING_TIME_FORMAT.parse(context.match), EditMeetingHandlerId.OPEN_MEETING_TIME_FORMAT
    ).id

    meeting = await guards.meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        action="open_meeting_time_format",
        context=context,
    )

    await context.api.edit_message(update=update, view=meeting_views.time_format_view(meeting))


@asynccontextmanager
async def toggle_meeting_setting(
    session: AsyncSession,
    update: Update,
    context: TMitupContext,
    handler_id: EditMeetingHandlerId,
    callback_data: cb.CallbackData,
    return_view: Callable[[Meetup], MitupView],
) -> AsyncGenerator[tuple[Meetup, User]]:
    user = await guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(callback_data.parse(context.match), handler_id).id

    meeting = await guards.meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        action=handler_id.name,
        context=context,
    )

    yield meeting, user

    await context.api.edit_message(update=update, view=return_view(meeting))
    # Update all messages to ensure any visible message contains the new changes but skip current one
    # to stay in the current sub-screen view.
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


def create_meeting_settings_toggle_handler(
    handler_id: EditMeetingHandlerId,
    callback_data: cb.CallbackData,
    attribute: str,
    return_view: Callable[[Meetup], MitupView],
):
    @HandlersRegistry.register_callback_query(handler_id, callback_data=callback_data)
    @with_session(write=True)
    async def handler(session: AsyncSession, update: Update, context: TMitupContext):
        async with toggle_meeting_setting(
            session=session,
            update=update,
            context=context,
            handler_id=handler_id,
            callback_data=callback_data,
            return_view=return_view,
        ) as (meeting, user):
            old_value = getattr(meeting, attribute)
            setattr(meeting, attribute, not old_value)
            # One line in the factory instruments every boolean it builds a toggle for; the
            # `field` facet is the attribute name, so a new toggle is recorded by construction.
            log.info(
                "Meeting setting toggled",
                user_id=user.db_id,
                field=attribute,
                old_value=old_value,
                new_value=not old_value,
                reason="owner_toggled",
            )

    return handler


create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
    callback_data=cb.SET_MEETING_WAITING_LIST,
    attribute="waiting_list",
    return_view=meeting_views.behavior_view,
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
    callback_data=cb.SET_MEETING_PUBLIC,
    attribute="public",
    return_view=meeting_views.behavior_view,
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
    callback_data=cb.SET_MEETING_ALLOW_INVITATIONS,
    attribute="allow_invitation",
    return_view=meeting_views.behavior_view,
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK,
    callback_data=cb.SET_MEETING_INCOGNITO,
    attribute="incognito",
    return_view=meeting_views.behavior_view,
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_SHOW_TIMEZONE_CALLBACK,
    callback_data=cb.SET_MEETING_SHOW_TIMEZONE,
    attribute="show_timezone",
    return_view=meeting_views.time_format_view,
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_CLOCK_24H_CALLBACK,
    callback_data=cb.SET_MEETING_CLOCK_24H,
    attribute="clock_24h",
    return_view=meeting_views.time_format_view,
)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCK_ON_START_CALLBACK, callback_data=cb.SET_MEETING_LOCK_ON_START
)
@with_session(write=True)
async def callback_query_set_lock_on_start(session: AsyncSession, update: Update, context: TMitupContext):
    async with toggle_meeting_setting(
        session=session,
        update=update,
        context=context,
        handler_id=EditMeetingHandlerId.LOCK_ON_START_CALLBACK,
        callback_data=cb.SET_MEETING_LOCK_ON_START,
        return_view=meeting_views.behavior_view,
    ) as (meeting, user):
        # A dedicated event rather than the factory's: the start time is on the line because
        # whether the rule can ever fire depends on there being one.
        log.info(
            "Meeting lock on start toggled",
            user_id=user.db_id,
            old_value=meeting.lock_on_start,
            new_value=not meeting.lock_on_start,
            meeting_datetime=meeting.datetime,
            reason="owner_toggled",
        )
        meeting.lock_on_start = not meeting.lock_on_start


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.SET_MEETING_DATE_FORMAT_CALLBACK, callback_data=cb.SET_MEETING_DATE_FORMAT
)
@with_session(write=True)
async def callback_query_set_meeting_date_format(session: AsyncSession, update: Update, context: TMitupContext):
    valid_data = guards.valid_meeting_callback_data(
        cb.SET_MEETING_DATE_FORMAT.parse(context.match), EditMeetingHandlerId.SET_MEETING_DATE_FORMAT_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, valid_data.meeting_id, "Set meeting date format", context)

    old_format = meeting.date_format
    new_format = list(DateFormat)[valid_data.id]
    log.info(
        "Meeting date format set",
        user_id=user.db_id,
        old_format=old_format.value,
        new_format=new_format.value,
        reason="owner_selected",
    )
    meeting.date_format = new_format

    await context.api.edit_message(update=update, view=meeting_views.time_format_view(meeting))

    # Skipping the current message keeps the user on the time format card.
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
