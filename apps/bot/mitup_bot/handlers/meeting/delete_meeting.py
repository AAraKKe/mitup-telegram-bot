from typing import cast

from sqlmodel import col, delete
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_MEETING_CALLBACK, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_session
async def callback_query_delete_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING.parse(context.match), MeetingHandlerId.DELETE_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    await guards.meeting(
        session,
        user,
        callback_data.id,
        "Delete meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            guards.render_context(user, update, context),
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(callback_data.id),
            decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(callback_data.id),
        ),
    )


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING, bindable=True
)
@with_session(write=True)
async def callback_query_confirm_delete_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
    )

    user = await guards.current_user(update, session)

    # lock: the invited-user cleanup below reads joined_links and the DELETE races with concurrent
    # joins (a link inserted between the read and the delete would leak its invited user), so the
    # meeting is resolved under the per-meeting row lock.
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Confirm delete meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
        lock=True,
    )

    # Rendered (and queued) before the rows are deleted below; the edits themselves run after
    # the deletion commits.
    await context.api.update_meeting_messages(meeting=meeting, was_deleted=True)

    # Keep all invited users ides to also delete them
    invited_users_ids = [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]
    await session.exec(delete(User).where(col(User.id).in_(invited_users_ids)))
    await session.delete(meeting)

    view = MitupView(
        description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING, bindable=True
)
@with_session
async def callback_query_decline_delete_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING.parse(context.match),
        MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "Decline delete meeting", context)

    await context.api.edit_message(
        update=update,
        view=meeting_views.main_view(meeting).with_context(
            MeetingLifecycleMessages.DELETE_DECLINED.get(lang=user.lang)
        ),
    )
