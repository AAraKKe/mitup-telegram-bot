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
from mitup_bot.views import MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId


def duration_input_prompt_view(meeting_id: int, lang: str, current_duration: int | None) -> MitupView:
    if current_duration is not None:
        description = MeetingMessages.EDIT_DURATION_PROMPT.get(lang=lang, current_duration=current_duration)
    else:
        description = MeetingMessages.SET_DURATION_PROMPT.get(lang=lang)
    return factory.request_information_with_cancel_view(
        lang=lang,
        message=description,
        callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_DURATION
)
@with_async_session
async def callback_query_edit_meeting_duration(session: Session, update: Update, context: TMitupContext) -> None:
    logging.debug("Enter into callback_query_edit_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_ENTRY_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "edit_meeting_duration", update, context)
    if meeting is None:
        return

    await context.api.edit_message(update=update, view=meeting.duration_view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_INPUT_CALLBACK, callback_data=cb.SET_MEETING_DURATION, bindable=False
)
@with_async_session
async def callback_query_set_meeting_duration(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    logging.debug("Enter into callback_query_set_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_INPUT_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "set_meeting_duration", update, context)
    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DURATION, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DURATION,
        MeetingMessages.EDIT_MEETING_DURATION_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
    )

    await context.api.edit_message(
        update=update,
        view=duration_input_prompt_view(meeting_id, user.lang, meeting.duration_minutes),
    )

    return ConversationMeetingState.EDIT_DURATION


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_TEXT_MESSAGE, filters=PositiveNumberFilter(), bindable=False
)
@with_async_session
async def duration_text_message_handler(session: Session, update: Update, context: TMitupContext) -> int:
    logging.debug("Enter into duration_text_message_handler")

    number_text = guards.message(update).text
    user = guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_DURATION) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "set_meeting_duration", update, context)
            if meeting is None:
                return ConversationHandler.END
    except ContextPropertyNotSetError as exc:
        logging.error(exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    meeting.duration_minutes = int(cast(str, number_text))
    session.flush()

    response_view = meeting.duration_view.with_context(
        MeetingMessages.DURATION_SET_SUCCESS.get(lang=user.lang, duration=meeting.duration_minutes)
    )

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(session=session, meeting=meeting)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_INVALID_MESSAGE, filters=~PositiveNumberFilter(), bindable=False
)
@with_async_session
async def duration_invalid_message_handler(
    session: Session, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    user = guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_DURATION, ensure_clean=False) as meeting_id:
            meeting = await guards.user_owns_meeting(user, meeting_id, "set_meeting_duration", update, context)
            if meeting is None:
                return ConversationHandler.END
            prompt_view = duration_input_prompt_view(meeting_id, user.lang, meeting.duration_minutes)
    except ContextPropertyNotSetError as exc:
        logging.error(exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    await context.api.send_message(update=update, view=prompt_view)

    return ConversationMeetingState.EDIT_DURATION


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_CANCEL_CALLBACK, callback_data=cb.CANCEL_EDIT_MEETING_DURATION, bindable=False
)
@with_async_session
async def callback_query_cancel_edit_meeting_duration(session: Session, update: Update, context: TMitupContext) -> int:
    logging.debug("Enter into callback_query_cancel_edit_meeting_duration")

    context.clean_all_user_data()

    meeting_id = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_CANCEL_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session, user, meeting_id, "cancel_edit_meeting_duration", update, context
    )
    if meeting is None:
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=meeting.duration_view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_CLEAR_CALLBACK, callback_data=cb.CLEAR_MEETING_DURATION
)
@with_async_session
async def callback_query_clear_meeting_duration(session: Session, update: Update, context: TMitupContext) -> None:
    logging.debug("Enter into callback_query_clear_meeting_duration")

    meeting_id = guards.valid_callback_data(
        cb.CLEAR_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_CLEAR_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "clear_meeting_duration", update, context)
    if meeting is None:
        return

    meeting.duration_minutes = None
    meeting.lock_on_start = False
    session.flush()

    response_view = meeting.duration_view.with_context(MeetingMessages.DURATION_CLEARED.get(lang=user.lang))

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(session=session, meeting=meeting)


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.DURATION_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DURATION_INPUT_CALLBACK],
    states={
        ConversationMeetingState.EDIT_DURATION: [
            EditMeetingHandlerId.DURATION_TEXT_MESSAGE,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.DURATION_INVALID_MESSAGE],
)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCK_ON_START_CALLBACK, callback_data=cb.SET_MEETING_LOCK_ON_START
)
@with_async_session
async def callback_query_set_lock_on_start(session: Session, update: Update, context: TMitupContext) -> None:
    logging.debug("Enter into callback_query_set_lock_on_start")

    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_LOCK_ON_START.parse(context.match), EditMeetingHandlerId.LOCK_ON_START_CALLBACK
    ).id
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(session, user, meeting_id, "set_lock_on_start", update, context)
    if meeting is None:
        return

    if meeting.duration_minutes is None:
        await context.api.answer_callback_query(
            update,
            text=MeetingMessages.LOCK_ON_START_STALE_ALERT.get_text(lang=meeting.user_language),
            show_alert=True,
        )
        return

    meeting.lock_on_start = not meeting.lock_on_start
    session.flush()

    await context.api.edit_message(update=update, view=meeting.duration_view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
