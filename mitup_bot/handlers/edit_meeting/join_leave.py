from collections.abc import Awaitable, Callable

from sqlmodel import Session
from telegram import Update

from mitup_bot import api, guards
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import EffectiveUserNotSet, UserNotFound
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup, Message, User, utils
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import EditMeetingHandlerId


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.JOIN, callback_data=cb.JOIN)
@with_async_session
async def join_meetup(session: Session, update: Update, context: TMitupContext):
    """
    Handle the join action when clicked on a meeting. This action can be clicked by any user
    to whom the meeting has been shared to.

    If the user is not registered we should ask the user to register by opening a chat with the bot first.
    """
    try:
        user = guards.current_user(update, session)
        await user_joins_meeting(session, update, context, user)
    except UserNotFound:
        await handle_non_existing_user_join(session, update, context)


async def user_joins_meeting(
    session: Session, update: Update, context: TMitupContext, user: User, with_notification: bool = True
):
    """
    The provided user joins the meeting.
    """

    async def join_operation(meeting: Meetup, user: User) -> MeetingMessages:
        if not user.joined_meeting(meeting.db_id):
            if (joined_link := meeting.add_participant(user)) is not None:
                session.add(joined_link)
                context.put_feature_metric(Feature.JOIN_MEETING)
                return (
                    MeetingMessages.JOINED_MEETING_FULL_WAITING_LIST
                    if joined_link.is_waiting_list
                    else MeetingMessages.JOINED_MEETING_SUCCESS
                )
            else:
                return MeetingMessages.JOINED_MEETING_FULL

        return MeetingMessages.JOINED_MEETING_ALREADY

    await handle_join_leave_operation(session, update, context, user, join_operation, with_notification)


def register_default_user(session: Session, update: Update) -> User:
    """
    Register the user with default values.
    """
    if update.effective_user is None:  # pragma: no cover
        raise EffectiveUserNotSet(update)

    new_user = utils.user_from_update(update)
    session.add(new_user)
    session.flush()

    return new_user


async def handle_non_existing_user_join(session: Session, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to join a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    user = register_default_user(session, update)
    await user_joins_meeting(session, update, context, user, with_notification=False)
    await api.answer_callback_query(
        context=context,
        update=update,
        text=MeetingMessages.JOINED_MEETING_UNREGISTERED.get(plain=True),
        show_alert=True,
    )


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.LEAVE, callback_data=cb.LEAVE)
@with_async_session
async def leave_meetup(session: Session, update: Update, context: TMitupContext):
    """
    Handle the leave action when clicked on a meeting. This action can be clicked by any user
    who has already joined the meeting. If the user is not registered we should ask the user
    to register by opening a chat with the bot first.
    """
    try:
        user = guards.current_user(update, session)
        await user_leaves_meeting(session, update, context, user)
    except UserNotFound:
        await handle_non_existing_user_leave(session, update, context)


async def user_leaves_meeting(
    session: Session, update: Update, context: TMitupContext, user: User, with_notification: bool = True
):
    async def leave_operation(meeting: Meetup, user: User) -> MeetingMessages:
        if joined_link := meeting.participant(user.db_id):
            promoted_links = meeting.remove_participant(joined_link)
            context.put_feature_metric(Feature.LEAVE_MEETING)

            promoted_users = [link.user for link in promoted_links]

            if promoted_users:
                views_to_send = [
                    MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(lang=user.lang, meeting_title=meeting.title)
                    for user in promoted_users
                ]

                await api.send_messages_to_users(
                    context,
                    promoted_users,
                    views_to_send,
                )

            return MeetingMessages.LEFT_MEETING_SUCCESS

        return MeetingMessages.LEFT_MEETING_ALREADY

    await handle_join_leave_operation(session, update, context, user, leave_operation, with_notification)


async def handle_non_existing_user_leave(session: Session, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to leave a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    user = register_default_user(session, update)
    await user_leaves_meeting(session, update, context, user, with_notification=False)
    await api.answer_callback_query(
        context=context,
        update=update,
        text=MeetingMessages.LEFT_MEETING_UNREGISTERED.get(plain=True),
        show_alert=True,
    )


async def handle_join_leave_operation(
    session: Session,
    update: Update,
    context: TMitupContext,
    user: User,
    operation: Callable[[Meetup, User], Awaitable[MeetingMessages]],
    with_notification: bool = True,
):
    """Handle common infrastructure for meeting operations (join/leave)."""
    data = guards.valid_callback_data(cb.JOIN.parse(context.match), EditMeetingHandlerId.JOIN)
    if meeting := Meetup.by_id(session, data.id):
        # Common message handling
        if (current_message := meeting.message_from_update(update)) is None:
            current_message = Message.from_update(update, meeting, user)
            meeting.messages.append(current_message)

        # Execute core operation
        notification_key = await operation(meeting, user)

        if with_notification:
            await api.answer_callback_query(
                context=context,
                update=update,
                text=notification_key.get(lang=user.lang, plain=True),
                show_alert=False,
            )

        session.flush()

        await api.update_meeting_messages(
            session=session, context_or_bot=context, meeting=meeting, current_message=current_message
        )
    else:
        # The meeting was not found, update the message to inform the user
        # This should never happen because when the meeting is deleted all messages are updated
        await api.edit_message(
            context=context, update=update, view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user.lang)
        )
        context.emit_metric(MetricKey.STALE_MEETING_MESSAGE, include_handler_dimensions=False)
