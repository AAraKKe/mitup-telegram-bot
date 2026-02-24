import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.models import Meetup, User
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


def _past_meeting_view(meeting: Meetup, user: User) -> MitupView:
    description = MeetingMessages.PAST_MEETING_DESCRIPTION.get(lang=user.lang)
    return MitupView(
        meeting.message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get(lang=user.lang),
                    callback_data=cb.REACTIVATE_MEETING.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.get(lang=user.lang),
                    callback_data=cb.DELETE_PAST_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
                ),
            ],
        ],
    ).with_context(description)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK, callback_data=cb.DELETE_PAST_MEETING, bindable=True
)
@with_async_session
async def callback_query_delete_past_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_delete_past_meeting")

    callback_data = guards.valid_callback_data(
        cb.DELETE_PAST_MEETING.parse(context.match), MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Delete past meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await context.api.send_message(
        update=update,
        view=MitupView(
            description=MeetingMessages.DELETE_MEETING.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CONFIRM.get(lang=user.lang),
                        callback_data=cb.CONFIRM_DELETE_PAST_MEETING.with_id(callback_data.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DECLINE.get(lang=user.lang),
                        callback_data=cb.DECLINE_DELETE_PAST_MEETING.with_id(callback_data.id),
                    ),
                ]
            ],
        ),
    )


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, callback_data=cb.SHOW_PAST_MEETING, bindable=True
)
@with_async_session
async def callback_query_show_past_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_show_past_meeting")

    callback_data = guards.valid_callback_data(
        cb.SHOW_PAST_MEETING.parse(context.match), MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Show past meeting",
        update,
        context,
    )
    if meeting is None:
        return

    full_meeting = Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    await context.api.edit_message(update=update, view=_past_meeting_view(full_meeting, user))


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK,
    callback_data=cb.CONFIRM_DELETE_PAST_MEETING,
    bindable=True,
)
@with_async_session
async def callback_query_confirm_delete_past_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_confirm_delete_past_meeting")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_PAST_MEETING.parse(context.match),
        MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK,
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Confirm delete past meeting",
        update,
        context,
    )
    if meeting is None:
        return

    full_meeting = Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    await context.api.update_meeting_messages(session=session, meeting=full_meeting, was_deleted=True)

    session.delete(full_meeting)

    view = MitupView(
        description=MeetingMessages.DELETE_MEETING_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
                )
            ]
        ],
    )
    await context.api.send_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK,
    callback_data=cb.DECLINE_DELETE_PAST_MEETING,
    bindable=True,
)
@with_async_session
async def callback_query_decline_delete_past_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_decline_delete_past_meeting")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_PAST_MEETING.parse(context.match),
        MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK,
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Decline delete past meeting",
        update,
        context,
    )
    if meeting is None:
        return

    full_meeting = Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    await context.api.edit_message(update=update, view=_past_meeting_view(full_meeting, user))
