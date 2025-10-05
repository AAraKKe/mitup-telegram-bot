import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import api, guards
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_MEETING_CALLBACK, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.debug("Enter into callback_query_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING.parse(context.match), MeetingHandlerId.DELETE_MEETING_CALLBACK
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.send_message(
        context=context,
        update=update,
        view=MitupView(
            description=MeetingMessages.DELETE_MEETING.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CONFIRM.get(lang=user.lang),
                        callback_data=cb.CONFIRM_DELETE_MEETING.with_id(callback_data.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DECLINE.get(lang=user.lang),
                        callback_data=cb.DECLINE_DELETE_MEETING.with_id(callback_data.id),
                    ),
                ]
            ],
        ),
    )


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_confirm_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.debug("Enter into callback_query_confirm_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Confirm delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.update_meeting_messages(session=session, context_or_bot=context, meeting=meeting, was_deleted=True)

    session.delete(meeting)

    view = MitupView(
        description=MeetingMessages.DELETE_MEETING_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )
    await api.send_message(context=context, update=update, view=view)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_decline_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.debug("Enter into callback_query_decline_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Decline delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.edit_message(
        context=context,
        update=update,
        view=meeting.main_view.with_context(MeetingMessages.DELETE_MEETING_DECLINE.get(lang=user.lang)),
    )
