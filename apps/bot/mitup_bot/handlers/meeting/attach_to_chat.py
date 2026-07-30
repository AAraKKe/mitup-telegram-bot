import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.utils import MeetingAttachMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import MeetingHandlerId

log = structlog.get_logger(__name__)


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

    The button rides on every card the meeting was shared into, so the caller may be any Telegram
    user, with or without a mitup profile: what the tap decides is whether the meeting is findable
    in that chat, and whoever taps it already holds the card. No account is created for them.
    """
    data = guards.valid_callback_data(cb.ATTACH_TO_CHAT.parse(context.match), MeetingHandlerId.ATTACH_TO_CHAT)
    # load_collections: `keyboard_for_update` picks the owner or the external keyboard via
    # `user.own_meeting` when the tap comes from a bot-chat message.
    user = (
        await User.by_tg_user_id(session, update.effective_user.id, load_collections=True)
        if update.effective_user
        else None
    )
    if user is not None and user.status is UserStatus.DELETION_REQUESTED:
        # A dying account decides nothing here: the attachment belongs to the chat, so treat them
        # as the anonymous caller the surface already allows.
        user = None

    # require_active: a finished meeting stays worth finding in the chat it happened in, so the
    # attachment is offered for whatever state the meeting is in.
    meeting = await guards.shared_meeting(
        session,
        user,
        data.id,
        "attach a meeting to a chat",
        update,
        allow_anonymous=True,
        require_active=False,
    )

    chat_instance = update.callback_query.chat_instance if update.callback_query else None
    already_attached = is_already_attached(meeting, chat_instance)

    if (current_message := meeting.message_from_update(update)) is None:
        current_message = Message.from_update(update, meeting, meeting_views.keyboard_for_update(update, meeting, user))
        meeting.messages.append(current_message)
        outcome = "already_attached" if already_attached else "message_linked"
    else:
        had_chat_instance = current_message.chat_instance is not None
        current_message.capture_chat_instance(update)
        if already_attached:
            outcome = "already_attached"
        elif not had_chat_instance and current_message.chat_instance is not None:
            outcome = "chat_instance_backfilled"
        else:
            outcome = "message_linked"

    # Not defensive: the broadcast payload snapshots message.id at enqueue time, and a
    # freshly appended Message only gets one from this flush (needed for the dead-message
    # reconcile if Telegram reports the message gone during the fan-out).
    await session.flush()

    # What the tap decided is whether the meeting is findable in this chat, and only the stored
    # chat instance makes it so — hence both the outcome and whether one is now held. The caller
    # may have no account, so `user_id` is genuinely absent rather than omitted.
    log.info(
        "Meeting attached to chat",
        user_id=user.db_id if user is not None else None,
        outcome=outcome,
        chat_instance_present=chat_instance is not None,
        messages_count=len(meeting.messages),
    )

    alert = MeetingAttachMessages.ALREADY_ENABLED_ALERT if already_attached else MeetingAttachMessages.ENABLED_ALERT
    await context.api.answer_callback_query(
        update=update,
        text=alert.get(),
        show_alert=True,
    )

    await context.api.update_meeting_messages(meeting=meeting, current_message=current_message)
    context.put_feature_metric(Feature.ATTACH_TO_CHAT)
