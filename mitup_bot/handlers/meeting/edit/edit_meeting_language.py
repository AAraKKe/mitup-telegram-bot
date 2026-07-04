from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.monitoring import Feature
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingEditLanguageMessages
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import EditMeetingHandlerId


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.LANGUAGE_CALLBACK, callback_data=cb.EDIT_MEETING_LANGUAGE
)
@with_session
async def callback_edit_meeting_language(session: AsyncSession, update: Update, context: TMitupContext):
    valid_data = guards.valid_callback_data(
        cb.EDIT_MEETING_LANGUAGE.parse(context.match), EditMeetingHandlerId.LANGUAGE_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, valid_data.id, "Edit meeting language", update, context)

    if meeting is None:
        return

    await context.api.edit_message(
        update=update,
        view=views.factory.meeting_set_language_view(meeting=meeting),
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.SET_LANGUAGE_CALLBACK, callback_data=cb.SET_MEETING_LANGUAGE
)
@with_session(write=True)
async def callback_set_meeting_language(session: AsyncSession, update: Update, context: TMitupContext):
    valid_data = guards.valid_meeting_callback_data(
        cb.SET_MEETING_LANGUAGE.parse(context.match), EditMeetingHandlerId.SET_LANGUAGE_CALLBACK
    )

    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session, user, valid_data.meeting_id, "Set meeting language", update, context
    )

    if meeting is None:
        return

    # Edit the meeting language and also all the keyboard markups for any of the shared messages
    # This is needed because the keyboard markup is stored in the database to ensure it is accessible
    # everywhere
    meeting.language = SUPPORTED_LANGUAGES[valid_data.id]
    for message in meeting.messages:
        message.buttons.keyboard = meeting.build_inline_keyboard(is_searchable=message.chat_instance is not None)

    await context.api.edit_message(
        update=update,
        view=views.factory.meeting_set_language_view(meeting=meeting).with_context(
            MeetingEditLanguageMessages.SUCCESS.get(lang=meeting.user_language)
        ),
    )

    # Since the language has changed, we need to update the messages of the meeting
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.MEETING_LANGUAGE_SET)
