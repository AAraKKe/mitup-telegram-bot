import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, limits
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import reply_rich_message_not_supported
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import capture_tagged_text
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import description_content

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import log_length_rejection, prepend_error

log = structlog.get_logger(__name__)

# The `action` facet the remove-description confirmation lines carry.
REMOVE_DESCRIPTION_ACTION = "remove_description"


def edit_description_prompt_view(meeting: Meetup, lang: str, *, error: RichContent | None = None) -> MitupView:
    """Build the description entry view.

    When ``error`` is given it is prepended as a leading paragraph, so a description that failed
    validation can be answered by resending this prompt with the error on top rather than a bare,
    button-less error.
    """
    current = description_content(meeting) or MeetingDisplayMessages.DESCRIPTION_EMPTY.rich(lang=lang)
    description: RichContent = MeetingEditContentMessages.DESCRIPTION_PROMPT.rich(lang=lang, description=current)
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description,
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
                )
            ]
        ],
    )


async def reject_long_description(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, length: int
) -> ConversationMeetingState:
    """Answer an over-long description with the prompt again, keeping the user in the edit state.

    The stored meeting id survives (``ensure_clean=False``) because the user is expected to resend a
    shorter description into this same conversation.
    """
    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, ensure_clean=False) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit description", context)

    log_length_rejection(context, user, field="description", length=length, limit=limits.DESCRIPTION_MAX_CHARS)

    error = MeetingEditContentMessages.DESCRIPTION_TOO_LONG.rich(
        lang=user.lang, length=length, limit=limits.DESCRIPTION_MAX_CHARS
    )
    await context.api.send_message(update=update, view=edit_description_prompt_view(meeting, user.lang, error=error))
    return ConversationMeetingState.EDIT_DESCRIPTION


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DESCRIPTION_CALLBACK, callback_data=cb.EDIT_MEETING_DESCRIPTION, bindable=False
)
@with_session
async def callback_query_edit_meeting_description(session: AsyncSession, update: Update, context: TMitupContext):
    assert context.matches is not None

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_DESCRIPTION.parse(context.match), EditMeetingHandlerId.DESCRIPTION_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit description",
        context,
    )

    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DESCRIPTION,
        MeetingEditContentMessages.DESCRIPTION_ON_EXIT,
        cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
        lang=user.lang,
    )

    await context.api.edit_message(update=update, view=edit_description_prompt_view(meeting, user.lang))

    return ConversationMeetingState.EDIT_DESCRIPTION


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_MESSAGE, filters.TEXT, bindable=False)
@with_session(write=True)
async def edit_description_meeting_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    message = guards.message(update)
    assert message.text is not None, "the TEXT filter this handler is registered with guarantees the text"

    user = await guards.current_user(update, session)

    length = len(message.text)
    if length > limits.DESCRIPTION_MAX_CHARS:
        return await reject_long_description(session, update, context, user, length)

    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit description", context)
        old_len = len(meeting.tagged_description or "")
        tagged = capture_tagged_text(message.text, message.entities, field="description")
        meeting.set_description(tagged)

        # Which meeting was edited and whether the owner grew or trimmed it — neither of which the
        # EMF property alone gives. Both lengths measure the stored tagged form, so they compare
        # like with like; the text itself is the owner's and never travels.
        log.info(
            "Meeting content edited",
            user_id=user.db_id,
            field="description",
            old_len=old_len,
            new_len=len(tagged),
            had_entities=bool(message.entities),
            reason="owner_edited",
        )

        await context.api.send_message(update=update, view=meeting_views.owner_view(meeting))
        await context.api.update_meeting_messages(meeting=meeting)

        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "description"})
        return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_RICH_MESSAGE, RichMessageFilter(), bindable=False)
@with_session
async def edit_description_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)
    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, ensure_clean=False) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit description", context)
    await reply_rich_message_not_supported(ctx, update, context, edit_description_prompt_view(meeting, user.lang))
    return ConversationMeetingState.EDIT_DESCRIPTION


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.DESCRIPTION_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DESCRIPTION_CALLBACK],
    states={
        ConversationMeetingState.EDIT_DESCRIPTION: [
            EditMeetingHandlerId.DESCRIPTION_MESSAGE,
            EditMeetingHandlerId.CANCEL,
        ],
    },
    fallbacks=[EditMeetingHandlerId.DESCRIPTION_RICH_MESSAGE, MessagesId.MESSAGE_WITHOUT_TEXT],
)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_DESCRIPTION_CALLBACK, callback_data=cb.DELETE_MEETING_DESCRIPTION
)
@with_session
async def callback_query_remove_description(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_DESCRIPTION.parse(context.match), EditMeetingHandlerId.REMOVE_DESCRIPTION_CALLBACK
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "remove_description", context)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=REMOVE_DESCRIPTION_ACTION)

    # A stale card can offer the remove chip after the description is already gone; the empty
    # placeholder keeps the confirmation honest about what the tap would erase.
    current = description_content(meeting) or MeetingDisplayMessages.DESCRIPTION_EMPTY.rich(lang=user.lang)
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditContentMessages.REMOVE_DESCRIPTION_CONFIRMATION.rich(lang=user.lang, description=current),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_DESCRIPTION.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_DESCRIPTION.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_DESCRIPTION_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_DESCRIPTION
)
@with_session(write=True)
async def callback_query_confirm_remove_description(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_DESCRIPTION.parse(context.match),
        EditMeetingHandlerId.CONFIRM_REMOVE_DESCRIPTION_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_description", context)

    # Written before the wipe, the last moment the length it records still measures anything; the
    # text itself is the owner's and never travels.
    log.info(
        "Meeting description removed",
        user_id=user.db_id,
        reason="owner_confirmed",
        previous_length=len(meeting.tagged_description or ""),
    )

    meeting.description = None

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "description_removed"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_DESCRIPTION_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_DESCRIPTION
)
@with_session
async def callback_query_decline_remove_description(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_DESCRIPTION.parse(context.match),
        EditMeetingHandlerId.DECLINE_REMOVE_DESCRIPTION_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_description", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_DESCRIPTION_ACTION)

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
