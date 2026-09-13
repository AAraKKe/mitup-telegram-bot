from typing import cast

import structlog
from sqlmodel import col, delete
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.api_wrapper import replace_message
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.utils import MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId

log = structlog.get_logger(__name__)

# The `action` facet both destructive-confirmation lines in this module carry.
DELETE_MEETING_ACTION = "delete_meeting"


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_MEETING_CALLBACK, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_session
async def callback_query_delete_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING.parse(context.match), MeetingHandlerId.DELETE_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Delete meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=DELETE_MEETING_ACTION)

    await context.api.edit_message(
        update=update,
        view=meeting_views.delete_prompt_view(
            guards.render_context(user, update, context),
            meeting,
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

    # The invited-user cleanup reads joined_links and the DELETE races with concurrent joins, so
    # the meeting is resolved under the per-meeting row lock.
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Confirm delete meeting",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
        lock=True,
    )

    # Rendered (and queued) before the rows are deleted below; the edits themselves run after the
    # deletion commits. The tapped message is left out because this handler replaces it itself.
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
        was_deleted=True,
    )

    invited_users_ids = [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]

    # The rows go for good, so this line is the only evidence any of them existed. Written before
    # the DELETEs, while the counts still have something to count.
    log.info(
        "Meeting deleted",
        user_id=user.db_id,
        reason="owner_confirmed",
        was_active=meeting.active,
        participants_count=meeting.n_participants,
        waiting_count=meeting.n_waiting,
        messages_count=len(meeting.messages),
        invited_users_deleted=len(invited_users_ids),
        invited_user_ids=invited_users_ids,
    )

    await session.exec(delete(User).where(col(User.id).in_(invited_users_ids)))
    await session.delete(meeting)

    view = factory.main_menu_view(
        guards.render_context(user, update, context),
        message=MeetingLifecycleMessages.DELETE_SUCCESS.rich(lang=user.lang),
        counts=await user.meeting_counts(session),
    )
    await replace_message(context.api, update, view)


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

    log.info("Meeting destructive action declined", user_id=user.db_id, action=DELETE_MEETING_ACTION)

    await context.api.edit_message(
        update=update,
        view=meeting_views.owner_view(meeting).with_context(
            MeetingLifecycleMessages.DELETE_DECLINED.rich(lang=user.lang)
        ),
    )
