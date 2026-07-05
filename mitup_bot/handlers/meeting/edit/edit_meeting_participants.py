from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.handlers.meeting.utils import participant_capacity_rejection
from mitup_bot.handlers.personal_filters import PositiveNumberFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import MeetingEditParticipantsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .views import edit_max_participants_view, edit_participants_view

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CALLBACK, callback_data=cb.EDIT_MEETING_PARTICIPANTS, bindable=True
)
@with_session
async def callback_edit_meeting_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit participants",
        update,
        context,
    )
    if meeting is None:
        return

    await context.api.edit_message(update=update, view=edit_participants_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK, callback_data=cb.EDIT_MEETING_MAX_PARTICIPANTS, bindable=False
)
@with_session
async def callback_edit_meeting_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_MAX_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit max participants",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_MAX_PARTICIPANTS,
        MeetingEditParticipantsMessages.MAX_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(callback_data.id),
    )

    await context.api.send_message(update=update, view=edit_max_participants_view(meeting))

    return ConversationMeetingState.EDIT_MAX_PARTICIPANTS


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
    callback_data=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS,
    bindable=False,
)
@with_session(write=True)
async def callback_edit_meeting_no_limit_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK
    )
    user = await guards.current_user(update, session)

    # for_update: capacity changes race with concurrent joins reading `full`, so the write
    # happens under the per-meeting row lock.
    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit no limit participants",
        update,
        context,
        for_update=True,
    )

    if meeting is None:
        return ConversationHandler.END

    meeting.max_members = None

    no_limit_text = MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=user.lang)
    response_view = edit_participants_view(meeting).with_context(
        MeetingEditParticipantsMessages.MAX_SUCCESS.get(max_participants=no_limit_text)
    )

    await context.api.send_message(update=update, view=response_view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS,
    bindable=False,
)
@with_session
async def callback_cancel_edit_meeting_participants(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data()

    await callback_edit_meeting_participants(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE, filters=PositiveNumberFilter(), bindable=False
)
@with_session(write=True)
async def edit_meeting_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    number = guards.message(update).text
    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "Edit max participants", update, context)
            if meeting is None:
                return ConversationHandler.END
    except ContextPropertyNotSetError as exc:
        log.error("Meeting id not set in context", exc_info=exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    # for_update: capacity changes race with concurrent joins reading `full`, so the write happens
    # under the per-meeting row lock. The ownership guard reads the relationship without touching
    # the DB; None means the meeting vanished since — end the conversation quietly.
    meeting = await Meetup.by_id(session, meeting.db_id, for_update=True)
    if meeting is None:
        return ConversationHandler.END

    requested_max = int(cast(str, number))

    # A free owner is capped by the free-tier participant limit; reject anything above it and keep
    # the conversation open so they can enter a valid number. Premium owners set any limit.
    if rejection := participant_capacity_rejection(user, requested_max):
        await context.api.send_message(update=update, view=edit_max_participants_view(meeting).with_context(rejection))
        return ConversationMeetingState.EDIT_MAX_PARTICIPANTS

    meeting.max_members = requested_max

    response_view = edit_participants_view(meeting).with_context(
        MeetingEditParticipantsMessages.MAX_SUCCESS.get(max_participants=meeting.max_members)
    )

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE, filters=~PositiveNumberFilter(), bindable=False
)
@with_session
async def edit_meeting_wrong_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, ensure_clean=False) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "Edit max participants", update, context)
            if meeting is None:
                return ConversationHandler.END
            response_view = edit_max_participants_view(meeting, fail=True)
    except ContextPropertyNotSetError as exc:
        log.error("Meeting id not set in context", exc_info=exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    await context.api.send_message(update=update, view=response_view)

    return ConversationMeetingState.EDIT_MAX_PARTICIPANTS


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK],
    states={
        ConversationMeetingState.EDIT_MAX_PARTICIPANTS: [
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
            EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
            EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE],
)
