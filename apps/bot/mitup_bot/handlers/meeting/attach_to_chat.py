from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import MeetingAttachMessages, MeetingDisplayMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


def is_already_attached(meeting: Meetup, chat_instance: str | None) -> bool:
    """Check if the meeting is already attached to this chat via another message."""
    if chat_instance is None:
        return False
    return any(m.chat_instance == chat_instance for m in meeting.messages)


@HandlersRegistry.register_callback_query(MeetingHandlerId.ATTACH_TO_CHAT, callback_data=cb.ATTACH_TO_CHAT)
@with_session(write=True)
async def attach_to_chat(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the 'Make it searchable' button click on a shared meeting.

    This captures the chat_instance from the callback query and associates it with the
    meeting message, making the meeting searchable in that chat via inline mode.
    """
    data = guards.valid_callback_data(cb.ATTACH_TO_CHAT.parse(context.match), MeetingHandlerId.ATTACH_TO_CHAT)
    user = await guards.current_user(update, session)

    if meeting := await Meetup.by_id(session, data.id):
        # Authorize before the chat_instance is captured: attaching makes the meeting inline-searchable
        # for everyone in that chat, and the meeting id arrives in client-supplied callback data.
        if not await guards.meeting_interaction_allowed(session, user, meeting, update, context):
            return

        chat_instance = update.callback_query.chat_instance if update.callback_query else None
        already_attached = is_already_attached(meeting, chat_instance)

        if (current_message := meeting.message_from_update(update)) is None:
            current_message = Message.from_update(
                update, meeting, meeting_views.keyboard_for_update(update, meeting, user)
            )
            meeting.messages.append(current_message)
        else:
            current_message.capture_chat_instance(update)

        # Not defensive: the broadcast payload snapshots message.id at enqueue time, and a
        # freshly appended Message only gets one from this flush (needed for the dead-message
        # reconcile if Telegram reports the message gone during the fan-out).
        await session.flush()

        alert = MeetingAttachMessages.ALREADY_ENABLED_ALERT if already_attached else MeetingAttachMessages.ENABLED_ALERT
        await context.api.answer_callback_query(
            update=update,
            text=alert.get(),
            show_alert=True,
        )

        await context.api.update_meeting_messages(meeting=meeting, current_message=current_message)
        context.put_feature_metric(Feature.ATTACH_TO_CHAT)
    else:
        await context.api.edit_message(
            update=update,
            view=MeetingDisplayMessages.DELETED_BANNER.get(lang=user.lang),
        )
        context.emit_metric(MetricKey.STALE_MEETING_MESSAGE, include_handler_properties=False)
