from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, factory
from mitup_bot.views.meeting_text import meeting_message

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


def past_meeting_view(meeting: Meetup, user: User, page: int) -> MitupView:
    description = MeetingLifecycleMessages.PAST_DESCRIPTION.get(lang=user.lang)
    return MitupView(
        meeting_message(meeting),
        [
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get_text(lang=user.lang),
                    callback_data=cb.REACTIVATE_MEETING.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.get_text(lang=user.lang),
                    callback_data=cb.DELETE_PAST_MEETING.with_page(meeting.db_id, page),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(page),
                ),
            ],
        ],
    ).with_context(description)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK, callback_data=cb.DELETE_PAST_MEETING, bindable=True
)
@with_session
async def callback_query_delete_past_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_paginated_callback_data(
        cb.DELETE_PAST_MEETING.parse(context.match), MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Delete past meeting", update, context)
    if meeting is None:
        return

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            guards.render_context(user, update, context),
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_PAST_MEETING.with_page(callback_data.id, callback_data.page),
            decline_callback_data=cb.DECLINE_DELETE_PAST_MEETING.with_page(callback_data.id, callback_data.page),
        ),
    )


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, callback_data=cb.SHOW_PAST_MEETING, bindable=True
)
@with_session
async def callback_query_show_past_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_paginated_callback_data(
        cb.SHOW_PAST_MEETING.parse(context.match), MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Show past meeting", update, context)
    if meeting is None:
        return

    full_meeting = await Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    await context.api.edit_message(update=update, view=past_meeting_view(full_meeting, user, callback_data.page))


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK,
    callback_data=cb.CONFIRM_DELETE_PAST_MEETING,
    bindable=True,
)
@with_session(write=True)
async def callback_query_confirm_delete_past_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_paginated_callback_data(
        cb.CONFIRM_DELETE_PAST_MEETING.parse(context.match), MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Confirm delete past meeting", update, context)
    if meeting is None:
        return

    full_meeting = await Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    # Rendered (and queued) before the row is deleted below; the edits themselves run after
    # the deletion commits.
    await context.api.update_meeting_messages(meeting=full_meeting, was_deleted=True)

    await session.delete(full_meeting)

    view = MitupView(
        description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(callback_data.page),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK,
    callback_data=cb.DECLINE_DELETE_PAST_MEETING,
    bindable=True,
)
@with_session
async def callback_query_decline_delete_past_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_paginated_callback_data(
        cb.DECLINE_DELETE_PAST_MEETING.parse(context.match), MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(user, callback_data.id, "Decline delete past meeting", update, context)
    if meeting is None:
        return

    full_meeting = await Meetup.by_id(session, callback_data.id, include_inactive=True)
    if full_meeting is None:
        return

    await context.api.edit_message(update=update, view=past_meeting_view(full_meeting, user, callback_data.page))
