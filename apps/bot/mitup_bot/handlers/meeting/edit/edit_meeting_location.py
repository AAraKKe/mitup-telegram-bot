import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, limits
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import recover_from_lost_context, reply_rich_message_not_supported
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import ButtonMessages, MeetingEditLocationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import MitupView

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import log_length_rejection, prepend_error
from .views import edit_location_view

log = structlog.get_logger(__name__)


def edit_location_name_prompt_view(
    meeting_id: int, lang: str, *, error: str | FormattedText | None = None
) -> MitupView:
    """Build the place-name entry view.

    When ``error`` is given it is prepended as a leading paragraph, so a name that failed validation
    can be answered by resending this prompt with the error on top rather than a bare, button-less
    error.
    """
    description: str | FormattedText = MeetingEditLocationMessages.NAME_PROMPT.get(lang=lang)
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                )
            ]
        ],
    )


async def reject_long_location_name(
    update: Update, context: TMitupContext, user: User, length: int
) -> ConversationMeetingState:
    """Answer an over-long place name with the prompt again, keeping the user in the edit state.

    The stored meeting id survives (``ensure_clean=False``) because the user is expected to resend a
    shorter name into this same conversation.
    """
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, ensure_clean=False) as meeting_id:
        error = MeetingEditLocationMessages.LOCATION_NAME_TOO_LONG.get(
            lang=user.lang, length=length, limit=limits.LOCATION_NAME_MAX_CHARS
        )
        view = edit_location_name_prompt_view(meeting_id, user.lang, error=error)

    log_length_rejection(context, user, field="location_name", length=length, limit=limits.LOCATION_NAME_MAX_CHARS)

    await context.api.send_message(update=update, view=view)
    return ConversationMeetingState.EDIT_LOCATION_NAME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_CALLBACK, callback_data=cb.EDIT_MEETING_LOCATION, bindable=True
)
@with_session
async def callback_edit_meeting_location(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_LOCATION.parse(context.match), EditMeetingHandlerId.LOCATION_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit location",
        context,
    )

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

    await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit location name",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    # Lets keep track of the meeting we are asking the name of the location for
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_LOCATION_NAME,
        MeetingEditLocationMessages.NAME_ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
    )

    await context.api.send_message(update=update, view=edit_location_name_prompt_view(callback_data.id, user.lang))

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

    await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit location coordinates",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

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
                        text=ButtonMessages.CANCEL.get_text(lang=user.lang),
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
    location_name = guards.message(update).text
    assert location_name is not None, "the TEXT filter this handler is registered with guarantees the text"

    user = await guards.current_user(update, session)

    length = len(location_name)
    try:
        if length > limits.LOCATION_NAME_MAX_CHARS:
            return await reject_long_location_name(update, context, user, length)

        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
            meeting = await guards.meeting(
                session,
                user,
                meeting_id,
                "Edit location name",
                context,
                access=guards.MeetingAccess.OWNER_ANY_STATE,
            )
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_NAME)

    had_previous_value = meeting.location.name is not None
    meeting.location.name = location_name

    # A venue change is what makes a card gain or lose its map link. The name and the coordinates
    # are the user's own — a home address, in the worst case — so only their presence travels.
    log.info(
        "Meeting location edited",
        user_id=user.db_id,
        field="name",
        coordinates_set=meeting.location.coordinates is not None,
        had_previous_value=had_previous_value,
        reason="owner_edited",
    )

    response_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.NAME_SUCCESS.get(name=meeting.location.name)
    )
    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "location_name"})
    return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE, filters.LOCATION, bindable=False)
@with_session(write=True)
async def edit_meeting_location_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    tg_location = guards.message(update).location

    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_COORDINATES) as meeting_id:
            meeting = await guards.meeting(
                session,
                user,
                meeting_id,
                "Edit location coordinates",
                context,
                access=guards.MeetingAccess.OWNER_ANY_STATE,
            )
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    assert tg_location is not None, "the LOCATION filter this handler is registered with guarantees the location"

    had_previous_value = meeting.location.coordinates is not None
    meeting.location.coordinates = (tg_location.longitude, tg_location.latitude)

    log.info(
        "Meeting location edited",
        user_id=user.db_id,
        field="coordinates",
        coordinates_set=True,
        had_previous_value=had_previous_value,
        reason="owner_edited",
    )

    response_view = edit_location_view(meeting).with_context(
        MeetingEditLocationMessages.COORDINATES_SUCCESS.get(lang=user.lang)
    )
    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "location_coordinates"})
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
                            text=ButtonMessages.CANCEL.get_text(lang=user.lang),
                            callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                        )
                    ]
                ],
            )
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_COORDINATES)

    await context.api.send_message(update=update, view=view)
    return ConversationMeetingState.EDIT_LOCATION_COORDIANTES


@HandlersRegistry.register_message(EditMeetingHandlerId.LOCATION_NAME_RICH_MESSAGE, RichMessageFilter(), bindable=False)
@with_session
async def edit_location_name_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, ensure_clean=False) as meeting_id:
        view = edit_location_name_prompt_view(meeting_id, user.lang)
    await reply_rich_message_not_supported(ctx, update, context, view)
    return ConversationMeetingState.EDIT_LOCATION_NAME


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.LOCATION_NAME_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.LOCATION_NAME_CALLBACK],
    states={
        ConversationMeetingState.EDIT_LOCATION_NAME: [
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
            EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.LOCATION_NAME_RICH_MESSAGE, MessagesId.MESSAGE_WITHOUT_TEXT],
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
