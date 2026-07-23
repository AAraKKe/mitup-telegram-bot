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
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views

from .enums import ConversationMeetingState, EditMeetingHandlerId


def edit_description_prompt_view(meeting: Meetup, lang: str) -> MitupView:
    description = (
        meeting.description if meeting.description else MeetingDisplayMessages.DESCRIPTION_EMPTY.get(lang=lang)
    )
    return MitupView(
        MeetingEditContentMessages.DESCRIPTION_PROMPT.get(lang=lang, description=description),
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
    EditMeetingHandlerId.DESCRIPTION_CALLBACK, callback_data=cb.EDIT_MEETING_DESCRIPTION, bindable=False
)
@with_session
async def callback_query_edit_meeting_description(session: AsyncSession, update: Update, context: TMitupContext):
    assert context.matches is not None

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_DESCRIPTION.parse(context.match), EditMeetingHandlerId.DESCRIPTION_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit description",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DESCRIPTION,
        MeetingEditContentMessages.DESCRIPTION_ON_EXIT.get(lang=user.lang),
        cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
    )

    await context.api.edit_message(update=update, view=edit_description_prompt_view(meeting, user.lang))

    return ConversationMeetingState.EDIT_DESCRIPTION


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_MESSAGE, filters.TEXT, bindable=False)
@with_session(write=True)
async def edit_description_meeting_message_handler(session: AsyncSession, update: Update, context: TMitupContext):
    assert update.effective_message is not None

    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION) as meeting_id:
        meeting = await Meetup.by_id(session, meeting_id, must_exist=True)
        meeting.description = update.effective_message.text

        view = meeting_views.edit_view(meeting).with_context(
            MeetingEditContentMessages.DESCRIPTION_SUCCESS.get(description=meeting.description)
        )
        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(meeting=meeting)

        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "description"})
        return ConversationHandler.END


@HandlersRegistry.register_message(EditMeetingHandlerId.DESCRIPTION_RICH_MESSAGE, RichMessageFilter(), bindable=False)
@with_session
async def edit_description_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session, load_collections=False)
    ctx = guards.render_context(user, update, context)
    with context.meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, ensure_clean=False) as meeting_id:
        meeting = await Meetup.by_id(session, meeting_id, must_exist=True)
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
