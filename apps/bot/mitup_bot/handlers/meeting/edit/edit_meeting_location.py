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
from mitup_bot.utils import ButtonMessages, CommonMessages, MeetingEditLocationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import log_length_rejection, log_stale_navigation, prepend_error

log = structlog.get_logger(__name__)

# The `action` facets the remove-location confirmation lines carry.
REMOVE_LOCATION_NAME_ACTION = "remove_location_name"
REMOVE_COORDINATES_ACTION = "remove_coordinates"


def edit_location_name_prompt_view(meeting_id: int, lang: str, *, error: RichContent | None = None) -> MitupView:
    """Build the place-name entry view.

    When ``error`` is given it is prepended as a leading paragraph, so a name that failed validation
    can be answered by resending this prompt with the error on top rather than a bare, button-less
    error.
    """
    description: RichContent = MeetingEditLocationMessages.NAME_PROMPT.rich(lang=lang)
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        message=description,
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=lang),
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
        error = MeetingEditLocationMessages.LOCATION_NAME_TOO_LONG.rich(
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

    # No current screen renders this button; it survives only on old messages, so the tap lands
    # on the editor card, which owns the name and map pin edits.
    log_stale_navigation(user, "edit_meeting_location")
    await context.api.edit_message(
        update=update,
        view=meeting_views.owner_view(meeting).with_context(CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user.lang)),
    )


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
        MeetingEditLocationMessages.NAME_ON_EXIT,
        cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
        lang=user.lang,
    )

    await context.api.edit_message(update=update, view=edit_location_name_prompt_view(callback_data.id, user.lang))

    return ConversationMeetingState.EDIT_LOCATION_NAME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_LOCATION,
    bindable=False,
)
@with_session
async def callback_cancel_edit_meeting_location_property(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_LOCATION.parse(context.match), EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Cancel edit location",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))

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
        MeetingEditLocationMessages.COORDINATES_ON_EXIT,
        cb.CANCEL_EDIT_MEETING_LOCATION.with_id(callback_data.id),
        lang=user.lang,
    )

    await context.api.edit_message(
        update=update,
        view=MitupView(
            message=MeetingEditLocationMessages.COORDINATES_PROMPT.rich(lang=user.lang),
            menu=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CANCEL.text(lang=user.lang),
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
        return await recover_from_lost_context(
            session, update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_NAME
        )

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

    await context.api.send_message(update=update, view=meeting_views.owner_view(meeting))
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
        return await recover_from_lost_context(
            session, update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_COORDINATES
        )

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

    await context.api.send_message(update=update, view=meeting_views.owner_view(meeting))
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
                message=MeetingEditLocationMessages.COORDINATES_INVALID.rich(lang=user.lang),
                menu=[
                    [
                        ButtonConfig(
                            text=ButtonMessages.CANCEL.text(lang=user.lang),
                            callback_data=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(meeting_id),
                        )
                    ]
                ],
            )
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(
            session, update, context, user, exc, ContextId.EDIT_MEETING_LOCATION_COORDINATES
        )

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


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_LOCATION_NAME_CALLBACK, callback_data=cb.DELETE_MEETING_LOCATION_NAME
)
@with_session
async def callback_query_remove_location_name(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_LOCATION_NAME.parse(context.match), EditMeetingHandlerId.REMOVE_LOCATION_NAME_CALLBACK
    )
    user = await guards.current_user(update, session)

    await guards.meeting(session, user, callback_data.id, "remove_location_name", context)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=REMOVE_LOCATION_NAME_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditLocationMessages.REMOVE_NAME_CONFIRMATION.rich(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_LOCATION_NAME.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_LOCATION_NAME.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_LOCATION_NAME_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_LOCATION_NAME
)
@with_session(write=True)
async def callback_query_confirm_remove_location_name(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_LOCATION_NAME.parse(context.match),
        EditMeetingHandlerId.CONFIRM_REMOVE_LOCATION_NAME_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_location_name", context)

    # The name itself is the user's own, a home address in the worst case, so only its presence
    # travels. Written before the wipe, so the presence it records is the one being erased.
    log.info(
        "Meeting location edited",
        user_id=user.db_id,
        field="name",
        coordinates_set=meeting.location.coordinates is not None,
        had_previous_value=meeting.location.name is not None,
        reason="owner_removed",
    )

    meeting.location.name = None

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "location_name_removed"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_LOCATION_NAME_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_LOCATION_NAME
)
@with_session
async def callback_query_decline_remove_location_name(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_LOCATION_NAME.parse(context.match),
        EditMeetingHandlerId.DECLINE_REMOVE_LOCATION_NAME_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_location_name", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_LOCATION_NAME_ACTION)

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_COORDINATES_CALLBACK, callback_data=cb.DELETE_MEETING_COORDINATES
)
@with_session
async def callback_query_remove_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_COORDINATES.parse(context.match), EditMeetingHandlerId.REMOVE_COORDINATES_CALLBACK
    )
    user = await guards.current_user(update, session)

    await guards.meeting(session, user, callback_data.id, "remove_coordinates", context)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=REMOVE_COORDINATES_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditLocationMessages.REMOVE_COORDINATES_CONFIRMATION.rich(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_COORDINATES.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_COORDINATES.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_COORDINATES_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_COORDINATES
)
@with_session(write=True)
async def callback_query_confirm_remove_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_COORDINATES.parse(context.match),
        EditMeetingHandlerId.CONFIRM_REMOVE_COORDINATES_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_coordinates", context)

    # Written before the wipe, so the presence it records is the one being erased.
    log.info(
        "Meeting location edited",
        user_id=user.db_id,
        field="coordinates",
        coordinates_set=False,
        had_previous_value=meeting.location.coordinates is not None,
        reason="owner_removed",
    )

    meeting.location.coordinates = None

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "location_coordinates_removed"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_COORDINATES_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_COORDINATES
)
@with_session
async def callback_query_decline_remove_coordinates(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_COORDINATES.parse(context.match),
        EditMeetingHandlerId.DECLINE_REMOVE_COORDINATES_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_coordinates", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_COORDINATES_ACTION)

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
