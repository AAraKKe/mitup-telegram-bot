from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import reply_rich_message_not_supported
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature
from mitup_bot.utils import ButtonMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import serialize_entities
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import rich_title

from .enums import ConversationMeetingState, EditMeetingHandlerId


def edit_title_prompt_view(meeting: Meetup, lang: str) -> MitupView:
    return MitupView(
        description=MeetingEditContentMessages.TITLE_PROMPT.get(title=rich_title(meeting)),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
                )
            ]
        ],
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.TITLE_CALLBACK, callback_data=cb.EDIT_MEETING_TITLE, bindable=False
)
@with_session
async def callback_query_edit_meeting_title(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(
        cb.EDIT_MEETING_TITLE.parse(context.match), EditMeetingHandlerId.TITLE_CALLBACK
    ).id

    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Edit title",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

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
    message = update.effective_message
    assert message is not None and message.text is not None

    with context.meeting_id(ContextId.EDIT_MEETING_TITLE) as meeting_id:
        meeting = await Meetup.by_id(session, meeting_id, must_exist=True)
        meeting.set_title(serialize_entities(message.text, message.entities))

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
    user = await guards.current_user(update, session, load_collections=False)
    ctx = guards.render_context(user, update, context)
    with context.meeting_id(ContextId.EDIT_MEETING_TITLE, ensure_clean=False) as meeting_id:
        meeting = await Meetup.by_id(session, meeting_id, must_exist=True)
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
