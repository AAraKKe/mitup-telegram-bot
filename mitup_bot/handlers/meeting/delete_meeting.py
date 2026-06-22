import logging
from typing import cast

from sqlmodel import Session, delete
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView, factory

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_MEETING_CALLBACK, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_delete_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING.parse(context.match), MeetingHandlerId.DELETE_MEETING_CALLBACK
    )

    user = guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Delete meeting", update, context)
    if meeting is None:
        return

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            lang=user.lang,
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(callback_data.id),
            decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(callback_data.id),
        ),
    )


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_confirm_delete_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_confirm_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
    )

    user = guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Confirm delete meeting", update, context)
    if meeting is None:
        return

    await context.api.update_meeting_messages(session=session, meeting=meeting, was_deleted=True)

    # Keep all invited users ides to also delete them
    invited_users_ids = [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]
    session.exec(delete(User).where(User.id.in_(invited_users_ids)))  # type: ignore
    session.delete(meeting)

    view = MitupView(
        description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_decline_delete_meeting(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_decline_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
    )
    user = guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Decline delete meeting", update, context)
    if meeting is None:
        return

    await context.api.edit_message(
        update=update,
        view=meeting.main_view.with_context(MeetingLifecycleMessages.DELETE_DECLINED.get(lang=user.lang)),
    )
