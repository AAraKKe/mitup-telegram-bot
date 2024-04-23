import logging

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import api, guards
from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import ContextPropertyNotSetError, MalformedCallbackData
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .views import edit_location_view


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_CALLBACK, callback_data=cb.EDIT_MEETING_LOCATION, bindable=True
)
@with_async_session
async def callback_edit_meeting_location(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_edit_meeting_location")
    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.EDIT_MEETING_LOCATION.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(EditMeetingHandlerId.LOCATION_CALLBACK, cb.EDIT_MEETING_LOCATION)

    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Edit location",
        update,
        context,
    )

    if meeting is None:
        return

    await api.edit_message(
        context,
        update,
        edit_location_view(meeting),
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_NAME_CALLBACK, callback_data=cb.EDIT_MEETING_LOCATION_NAME, bindable=False
)
@with_async_session
async def callback_edit_meeting_location_name(session: Session, update: Update, context: MitupContext):
    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.EDIT_MEETING_LOCATION_NAME.parse(context.matches[0]).id
    if meeting_id is None:
        raise MalformedCallbackData(EditMeetingHandlerId.LOCATION_NAME_CALLBACK, cb.EDIT_MEETING_LOCATION_NAME)

    meeting = await guards.user_owns_meeting(
        user,
        meeting_id,
        "Edit location name",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    # Lets keep track of the meeting we are asking the name of the location for
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, meeting_id)

    await api.send_message(
        context,
        update,
        MitupView(
            description=MeetingMessages.EDIT_MEETING_LOCATION_NAME.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(),
                        callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_LOCATION_NAME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION,
    bindable=False,
)
@with_async_session
async def callback_cancel_edit_meeting_location_property(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_cancel_edit_meeting_location_property")

    await callback_edit_meeting_location(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK,
    callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES,
    bindable=False,
)
@with_async_session
async def callback_edit_meeting_location_coordinates(session: Session, update: Update, context: MitupContext):
    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.EDIT_MEETING_LOCATION_COORDINATES.parse(context.matches[0]).id
    if meeting_id is None:
        raise MalformedCallbackData(
            EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK, cb.EDIT_MEETING_LOCATION_COORDINATES
        )

    meeting = await guards.user_owns_meeting(
        user,
        meeting_id,
        "Edit location coordinates",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    # Lets keep track of the meeting we are asking the name of the location for
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES, meeting_id)

    await api.send_message(
        context,
        update,
        MitupView(
            description=MeetingMessages.EDIT_MEETING_LOCATION_COORDINATES.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(),
                        callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_LOCATION_COORDIANTES


@HandlersRegistry.register_message(
    EditMeetingHandlerId.LOCATION_NAME_MESSAGE, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_async_session
async def edit_meeting_location_name(session: Session, update: Update, context: MitupContext):
    assert update.effective_message is not None

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
            meeting = Meetup.by_id(session, meeting_id, must_exist=True)
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        logging.error(exc)
        await api.edit_message(context, update, factory.main_menu_view())
        return ConversationHandler.END

    meeting.location.name = update.effective_message.text
    session.add(meeting)
    session.flush()

    response_view = edit_location_view(meeting).with_context(
        MeetingMessages.LOCATION_NAME_SET_SUCCESS.get(name=meeting.location.name)
    )
    await api.send_message(context, update, response_view)

    return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, filters.LOCATION, bindable=False)
@with_async_session
async def edit_meeting_location_coordinates(session: Session, update: Update, context: MitupContext):
    assert update.effective_message is not None

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES) as meeting_id:
            meeting = Meetup.by_id(session, meeting_id, must_exist=True)
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        logging.error(exc)
        await api.edit_message(context, update, factory.main_menu_view())
        return ConversationHandler.END

    tg_location = update.effective_message.location
    assert tg_location is not None

    meeting.location.coordinates = (tg_location.longitude, tg_location.latitude)
    session.add(meeting)
    session.flush()

    response_view = edit_location_view(meeting).with_context(MeetingMessages.LOCATION_COORDINATES_SUCCESS.get())
    await api.send_message(context, update, response_view)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE, ~filters.LOCATION, bindable=False
)
async def edit_coordinates_without_location(update: Update, context: MitupContext):
    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES, ensure_clean=False) as meeting_id:
            view = MitupView(
                description=MeetingMessages.LOCATION_COORDINATES_WRONG.get(),
                keyboard=[
                    [
                        ButtonConfig(
                            text=ButtonMessages.CANCEL.get(),
                            callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                        )
                    ]
                ],
            )
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        logging.error(exc)
        await api.edit_message(context, update, factory.main_menu_view())
        return ConversationHandler.END

    await api.send_message(context, update, view)
    return ConversationMeetingState.EDIT_LOCATION_COORDIANTES


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.LOCATION_NAME_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.LOCATION_NAME_CALLBACK],
    states={
        ConversationMeetingState.EDIT_LOCATION_NAME: [
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
            EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT],
)


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.LOCATION_COORDINATES_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK],
    states={
        ConversationMeetingState.EDIT_LOCATION_COORDIANTES: [
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
            EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE],
)
