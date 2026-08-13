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
from mitup_bot.utils import ButtonMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText, capture_tagged_text
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import rich_title

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import log_length_rejection, prepend_error

log = structlog.get_logger(__name__)


def edit_title_prompt_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the title entry view.

    When ``error`` is given it is prepended as a leading paragraph, so a title that failed
    validation can be answered by resending this prompt with the error on top rather than a bare,
    button-less error.
    """
    description: str | FormattedText = MeetingEditContentMessages.TITLE_PROMPT.get(lang=lang, title=rich_title(meeting))
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
                )
            ]
        ],
    )


async def reject_long_title(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, length: int
) -> ConversationMeetingState:
    """Answer an over-long title with the prompt again, keeping the user in the edit state.

    The stored meeting id survives (``ensure_clean=False``) because the user is expected to resend a
    shorter title into this same conversation.
    """
    with context.meeting_id(ContextId.EDIT_MEETING_TITLE, ensure_clean=False) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit title", context)

    log_length_rejection(context, user, field="title", length=length, limit=limits.TITLE_MAX_CHARS)

    error = MeetingEditContentMessages.TITLE_TOO_LONG.get(lang=user.lang, length=length, limit=limits.TITLE_MAX_CHARS)
    await context.api.send_message(update=update, view=edit_title_prompt_view(meeting, user.lang, error=error))
    return ConversationMeetingState.EDIT_TITLE


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.TITLE_CALLBACK, callback_data=cb.EDIT_MEETING_TITLE, bindable=False
)
@with_session
async def callback_query_edit_meeting_title(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_TITLE.parse(context.match), EditMeetingHandlerId.TITLE_CALLBACK
    ).id

    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        meeting_id,
        "Edit title",
        context,
    )

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TITLE,
        MeetingEditContentMessages.TITLE_ON_EXIT.get(lang=user.lang),
        cb.EDIT_MEETING_CANCEL.with_id(meeting_id),
    )

    await context.api.edit_message(update=update, view=edit_title_prompt_view(meeting, user.lang))

    return ConversationMeetingState.EDIT_TITLE


@HandlersRegistry.register_message(EditMeetingHandlerId.TITLE_MESSAGE, filters.TEXT, bindable=False)
@with_session(write=True)
async def edit_title_meeting_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    message = guards.message(update)
    assert message.text is not None, "the TEXT filter this handler is registered with guarantees the text"

    user = await guards.current_user(update, session)

    length = len(message.text)
    if length > limits.TITLE_MAX_CHARS:
        return await reject_long_title(session, update, context, user, length)

    with context.meeting_id(ContextId.EDIT_MEETING_TITLE) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit title", context)
        old_len = len(meeting.tagged_title)
        tagged = capture_tagged_text(message.text, message.entities, field="title")
        meeting.set_title(tagged)

        # Which meeting was edited and whether the owner grew or trimmed it — neither of which the
        # EMF property alone gives. Both lengths measure the stored tagged form, so they compare
        # like with like; the text itself is the owner's and never travels.
        log.info(
            "Meeting content edited",
            user_id=user.db_id,
            field="title",
            old_len=old_len,
            new_len=len(tagged),
            had_entities=bool(message.entities),
            reason="owner_edited",
        )

        view = meeting_views.edit_view(meeting).with_context(
            MeetingEditContentMessages.TITLE_SUCCESS.get(title=rich_title(meeting))
        )
        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(meeting=meeting)

        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "title"})
        return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.TITLE_RICH_MESSAGE, RichMessageFilter(), bindable=False)
@with_session
async def edit_title_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session)
    ctx = guards.render_context(user, update, context)
    with context.meeting_id(ContextId.EDIT_MEETING_TITLE, ensure_clean=False) as meeting_id:
        meeting = await guards.meeting(session, user, meeting_id, "Edit title", context)
    await reply_rich_message_not_supported(ctx, update, context, edit_title_prompt_view(meeting, user.lang))
    return ConversationMeetingState.EDIT_TITLE


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.TITLE_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.TITLE_CALLBACK],
    states={
        ConversationMeetingState.EDIT_TITLE: [
            EditMeetingHandlerId.TITLE_MESSAGE,
            EditMeetingHandlerId.CANCEL,
        ],
    },
    fallbacks=[EditMeetingHandlerId.TITLE_RICH_MESSAGE, MessagesId.MESSAGE_WITHOUT_TEXT],
)
