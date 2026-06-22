import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingEditWhenMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import EditMeetingHandlerId


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.WHEN_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_WHEN)
@with_async_session
async def callback_query_when_entry(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_when_entry")

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_WHEN.parse(context.match), EditMeetingHandlerId.WHEN_ENTRY_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "edit_meeting_when", update, context)
    if meeting is None:
        return

    await context.api.edit_message(update=update, view=meeting.when_view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CLEAR_TIMES_CALLBACK, callback_data=cb.DELETE_MEETING_TIMES
)
@with_async_session
async def callback_query_clear_times(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_clear_times")

    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.CLEAR_TIMES_CALLBACK
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, callback_data.id, "clear_times", update, context)
    if meeting is None:
        return

    view = factory.confirmation_view(
        lang=user.lang,
        message=MeetingEditWhenMessages.CLEAR_CONFIRMATION.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_TIMES.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_TIMES
)
@with_async_session
async def callback_query_confirm_clear_times(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_confirm_clear_times")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, callback_data.id, "confirm_clear_times", update, context)
    if meeting is None:
        return

    meeting.datetime = None
    meeting.end_datetime = None
    meeting.lock_on_start = False
    session.add(meeting)
    session.flush()

    view = meeting.when_view.with_context(MeetingEditWhenMessages.CLEAR_SUCCESS.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_TIMES
)
@with_async_session
async def callback_query_decline_clear_times(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_decline_clear_times")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_TIMES.parse(context.match), EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, callback_data.id, "decline_clear_times", update, context)
    if meeting is None:
        return

    view = meeting.when_view.with_context(MeetingEditWhenMessages.CLEAR_DECLINED.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCK_ON_START_CALLBACK, callback_data=cb.SET_MEETING_LOCK_ON_START
)
@with_async_session
async def callback_query_set_lock_on_start(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into callback_query_set_lock_on_start")

    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_LOCK_ON_START.parse(context.match), EditMeetingHandlerId.LOCK_ON_START_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "set_lock_on_start", update, context)
    if meeting is None:
        return

    if meeting.end_datetime is None:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditWhenMessages.LOCK_ON_START_ALERT.get_text(lang=meeting.user_language),
            show_alert=True,
        )
        return

    meeting.lock_on_start = not meeting.lock_on_start
    session.flush()

    await context.api.edit_message(update=update, view=meeting.when_view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
