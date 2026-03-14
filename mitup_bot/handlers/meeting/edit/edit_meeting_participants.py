import logging
from typing import cast

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.handlers.personal_filters import PositiveNumberFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .views import edit_max_participants_view, edit_participants_view


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CALLBACK, callback_data=cb.EDIT_MEETING_PARTICIPANTS, bindable=True
)
@with_async_session
async def callback_edit_meeting_participants(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_edit_meeting_participants")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_CALLBACK
    )

    user = guards.current_user(update, session)

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
@with_async_session
async def callback_edit_meeting_max_participants(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_edit_meeting_max_participants")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_MAX_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK
    )

    user = guards.current_user(update, session)

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
        MeetingMessages.EDIT_MEETING_MAX_PARTICIPANTS_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(callback_data.id),
    )

    await context.api.send_message(update=update, view=edit_max_participants_view(meeting))

    return ConversationMeetingState.EDIT_MAX_PARTICIPANTS


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
    callback_data=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS,
    bindable=False,
)
@with_async_session
async def callback_edit_meeting_no_limit_participants(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_edit_meeting_no_limit_participants")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit no limit participants",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    meeting.max_members = None
    session.flush()

    no_limit_text = MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=user.lang)
    response_view = edit_participants_view(meeting).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(max_participants=no_limit_text)
    )

    await context.api.send_message(update=update, view=response_view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS,
    bindable=False,
)
@with_async_session
async def callback_cancel_edit_meeting_participants(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_cancel_edit_meeting_participants_property")

    context.clean_all_user_data()

    await callback_edit_meeting_participants(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE, filters=PositiveNumberFilter(), bindable=False
)
@with_async_session
async def edit_meeting_max_participants(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into edit_meeting_max_participants")

    number = guards.message(update).text
    user = guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "Edit max participants", update, context)
            if meeting is None:
                return ConversationHandler.END
    except ContextPropertyNotSetError as exc:
        logging.error(exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    meeting.max_members = int(cast(str, number))
    session.flush()

    response_view = edit_participants_view(meeting).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(max_participants=meeting.max_members)
    )

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(session=session, meeting=meeting)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE, filters=~PositiveNumberFilter(), bindable=False
)
@with_async_session
async def edit_meeting_wrong_max_participants(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, ensure_clean=False) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "Edit max participants", update, context)
            if meeting is None:
                return ConversationHandler.END
            response_view = edit_max_participants_view(meeting, fail=True)
    except ContextPropertyNotSetError as exc:
        logging.error(exc)
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
