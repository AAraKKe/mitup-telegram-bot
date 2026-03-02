from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import EditMeetingHandlerId


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK, callback_data=cb.EDIT_MEETING_SETTINGS
)
@with_async_session
async def callback_query_edit_meeting_settings(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_SETTINGS.parse(context.match), EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK
    ).id

    if (
        meeting := await guards.meeting_accessible(
            session=session,
            user=user,
            meeting_id=meeting_id,
            action="edit_meeting_settings",
            update=update,
            context=context,
        )
    ) is not None:
        await context.api.edit_message(update=update, view=meeting.settings_view)


@asynccontextmanager
async def toggle_meeting_setting(
    session: Session,
    update: Update,
    context: TMitupContext,
    handler_id: EditMeetingHandlerId,
    callback_data: cb.CallbackData,
) -> AsyncGenerator[Meetup | None]:
    user = guards.current_user(update, session)

    meeting_id = guards.valid_callback_data(callback_data.parse(context.match), handler_id).id

    if (
        meeting := await guards.meeting_accessible(
            session=session,
            user=user,
            meeting_id=meeting_id,
            action=handler_id.name,
            update=update,
            context=context,
        )
    ) is not None:
        yield meeting
        session.flush()

        await context.api.edit_message(update=update, view=meeting.settings_view)
        # Update all messages to ensure any visible message contains the new changes but skip current one
        # if preseento to stay in the settings view.
        await context.api.update_meeting_messages(
            session=session,
            meeting=meeting,
            current_message=meeting.message_from_update(update),
            skip_current=True,
        )
    else:
        yield None


def create_meeting_settings_toggle_handler(
    handler_id: EditMeetingHandlerId, callback_data: cb.CallbackData, attribute: str
):
    @HandlersRegistry.register_callback_query(handler_id, callback_data=callback_data)
    @with_async_session
    async def handler(session: Session, update: Update, context: TMitupContext):
        async with toggle_meeting_setting(
            session=session,
            update=update,
            context=context,
            handler_id=handler_id,
            callback_data=callback_data,
        ) as meeting:
            if meeting is None:
                return
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
