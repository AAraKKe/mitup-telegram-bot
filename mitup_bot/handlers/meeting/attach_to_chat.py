from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.models import Meetup, Message
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId


def _is_already_attached(meeting: Meetup, chat_instance: str | None) -> bool:
    """Check if the meeting is already attached to this chat via another message."""
    if chat_instance is None:
        return False
    return any(m.chat_instance == chat_instance for m in meeting.messages)


@HandlersRegistry.register_callback_query(MeetingHandlerId.ATTACH_TO_CHAT, callback_data=cb.ATTACH_TO_CHAT)
@with_async_session
async def attach_to_chat(session: Session, update: Update, context: TMitupContext):
    """
    Handle the 'Make it searchable' button click on a shared meeting.

    This captures the chat_instance from the callback query and associates it with the
    meeting message, making the meeting searchable in that chat via inline mode.
    """
    data = guards.valid_callback_data(cb.ATTACH_TO_CHAT.parse(context.match), MeetingHandlerId.ATTACH_TO_CHAT)
    user = guards.current_user(update, session)

    if meeting := Meetup.by_id(session, data.id):
        chat_instance = update.callback_query.chat_instance if update.callback_query else None
        already_attached = _is_already_attached(meeting, chat_instance)

        if (current_message := meeting.message_from_update(update)) is None:
            current_message = Message.from_update(update, meeting, user)
            meeting.messages.append(current_message)
        elif current_message.chat_instance is None and chat_instance:
            current_message.chat_instance = chat_instance

        session.flush()

        alert = MeetingMessages.ALREADY_SEARCHABLE_ALERT if already_attached else MeetingMessages.NOW_SEARCHABLE_ALERT
        await context.api.answer_callback_query(
            update=update,
            text=alert.get(plain=True),
            show_alert=True,
        )

        await context.api.update_meeting_messages(session=session, meeting=meeting, current_message=current_message)
        context.put_feature_metric(Feature.ATTACH_TO_CHAT)
    else:
        await context.api.edit_message(
            update=update,
            view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user.lang),
        )
        context.emit_metric(MetricKey.STALE_MEETING_MESSAGE, include_handler_dimensions=False)
