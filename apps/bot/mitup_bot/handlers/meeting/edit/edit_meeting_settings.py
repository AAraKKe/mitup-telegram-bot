from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views

from .enums import EditMeetingHandlerId


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


@asynccontextmanager
async def toggle_meeting_setting(
    session: AsyncSession,
    update: Update,
    context: TMitupContext,
    handler_id: EditMeetingHandlerId,
    callback_data: cb.CallbackData,
    return_view: Callable[[Meetup], MitupView],
) -> AsyncGenerator[Meetup]:
    user = await guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(callback_data.parse(context.match), handler_id).id

    meeting = await guards.meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        action=handler_id.name,
        context=context,
    )

    yield meeting

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
    return_view: Callable[[Meetup], MitupView] = meeting_views.settings_view,
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
        ) as meeting:
            setattr(meeting, attribute, not getattr(meeting, attribute))

    return handler


create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
    callback_data=cb.SET_MEETING_WAITING_LIST,
    attribute="waiting_list",
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK, callback_data=cb.SET_MEETING_PUBLIC, attribute="public"
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
    callback_data=cb.SET_MEETING_ALLOW_INVITATIONS,
    attribute="allow_invitation",
)

create_meeting_settings_toggle_handler(
    EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK, callback_data=cb.SET_MEETING_INCOGNITO, attribute="incognito"
)
