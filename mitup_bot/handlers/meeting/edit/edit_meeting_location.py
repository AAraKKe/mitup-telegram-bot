import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.utils import ButtonMessages, MeetingEditLocationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .views import edit_location_view

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_CALLBACK, callback_data=cb.EDIT_MEETING_LOCATION, bindable=True
)
@with_session
async def callback_edit_meeting_location(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_LOCATION.parse(context.match), EditMeetingHandlerId.LOCATION_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit location",
        update,
        context,
    )

    if meeting is None:
        return

    await context.api.edit_message(update=update, view=edit_location_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_NAME_CALLBACK, callback_data=cb.EDIT_MEETING_LOCATION_NAME, bindable=False
)
@with_session
async def callback_edit_meeting_location_name(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_LOCATION_NAME.parse(context.match), EditMeetingHandlerId.LOCATION_NAME_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(
        user,
        callback_data.id,
        "Edit location name",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    # Lets keep track of the meeting we are asking the name of the location for
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_LOCATION_NAME,
        MeetingEditLocationMessages.NAME_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
    )

    await context.api.send_message(
        update=update,
        view=MitupView(
            description=MeetingEditLocationMessages.NAME_PROMPT.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(lang=user.lang),
                        callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
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
@with_session
async def callback_cancel_edit_meeting_location_property(session: AsyncSession, update: Update, context: TMitupContext):
    await callback_edit_meeting_location(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK,
    callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES,
    bindable=False,
)
@with_session
async def callback_edit_meeting_location_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_LOCATION_COORDINATES.parse(context.match),
        EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK,
    )

    user = await guards.current_user(update, session)

    meeting = await guards.user_owns_meeting(
        user,
        callback_data.id,
        "Edit location coordinates",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    # Lets keep track of the meeting we are asking the name of the location for
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_LOCATION_COORDINATES,
        MeetingEditLocationMessages.COORDINATES_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
    )

    await context.api.send_message(
        update=update,
        view=MitupView(
            description=MeetingEditLocationMessages.COORDINATES_PROMPT.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.get(lang=user.lang),
                        callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
                    )
                ]
            ],
        ),
    )

    return ConversationMeetingState.EDIT_LOCATION_COORDIANTES


@HandlersRegistry.register_message(
    EditMeetingHandlerId.LOCATION_NAME_MESSAGE, filters.TEXT & ~filters.COMMAND, bindable=False
)
@with_session(write=True)
async def edit_meeting_location_name(session: AsyncSession, update: Update, context: TMitupContext):
    assert update.effective_message is not None

    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
            meeting = await Meetup.by_id(session, meeting_id, must_exist=True)
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        log.error("Meeting id not set in context", exc_info=exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    meeting.location.name = update.effective_message.text

    response_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.NAME_SUCCESS.get(name=meeting.location.name)
    )
    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, filters.LOCATION, bindable=False)
@with_session(write=True)
async def edit_meeting_location_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    assert update.effective_message is not None

    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES) as meeting_id:
            meeting = await guards.user_owns_meeting(
                user, meeting_id, "Edit location coordinates", update, context, redirect=True
            )
            if meeting is None:
                return ConversationHandler.END
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        log.error("Meeting id not set in context", exc_info=exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    tg_location = update.effective_message.location
    assert tg_location is not None

    meeting.location.coordinates = (tg_location.longitude, tg_location.latitude)

    response_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.COORDINATES_SUCCESS.get(lang=user.lang)
    )
    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE, ~filters.LOCATION, bindable=False
)
@with_session
async def edit_coordinates_without_location(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES, ensure_clean=False) as meeting_id:
            view = MitupView(
                description=MeetingEditLocationMessages.COORDINATES_INVALID.get(lang=user.lang),
                keyboard=[
                    [
                        ButtonConfig(
                            text=ButtonMessages.CANCEL.get(lang=user.lang),
                            callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                        )
                    ]
                ],
            )
    except ContextPropertyNotSetError as exc:
        # If the meeting id is not set, we should not be here
        log.error("Meeting id not set in context", exc_info=exc)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
        return ConversationHandler.END

    await context.api.send_message(update=update, view=view)
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
