from collections.abc import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import racy_flush, with_session
from mitup_bot.exceptions import EffectiveUserNotSet, UserNotFound
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message, User, utils
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import MeetingDisplayMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views

from .enums import MeetingHandlerId


@HandlersRegistry.register_callback_query(MeetingHandlerId.JOIN, callback_data=cb.JOIN)
@with_session(write=True)
async def join_meetup(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the join action when clicked on a meeting. This action can be clicked by any user
    to whom the meeting has been shared to.

    If the user is not registered we should ask the user to register by opening a chat with the bot first.
    """
    try:
        user = await guards.current_user(update, session)
        await user_joins_meeting(session, update, context, user)
    except UserNotFound:
        await handle_non_existing_user_join(session, update, context)


async def user_joins_meeting(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, with_notification: bool = True
):
    """
    The provided user joins the meeting.
    """

    async def join_operation(meeting: Meetup, user: User) -> MeetingJoinMessages:
        if user.joined_meeting(meeting.db_id):
            return MeetingJoinMessages.JOIN_ALREADY_JOINED

        # Checked under the per-meeting row lock, so the answer cannot change before the
        # insert below — this is the only case where add_participant would produce nothing.
        if not meeting.join_allowed():
            return MeetingJoinMessages.JOIN_FULL

        joined_link = await racy_flush(
            session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
        )
        if joined_link is None:
            # A concurrent join already created the membership row; the unique constraint on
            # joined_users rejected our duplicate. Treat it as an idempotent no-op.
            return MeetingJoinMessages.JOIN_ALREADY_JOINED

        context.put_feature_metric(Feature.JOIN_MEETING)
        return (
            MeetingJoinMessages.JOIN_FULL_WAITING_LIST
            if joined_link.is_waiting_list
            else MeetingJoinMessages.JOIN_SUCCESS
        )

    await handle_join_leave_operation(session, update, context, user, join_operation, with_notification)


async def register_default_user(session: AsyncSession, update: Update) -> User:
    """
    Register the user with default values.
    """
    if update.effective_user is None:  # pragma: no cover
        raise EffectiveUserNotSet(update)

    new_user = utils.user_from_update(update, status=UserStatus.JOINED_ONLY)
    session.add(new_user)
    await session.flush()
    # The join/leave paths read the new user's collections right away (joined_meeting, and
    # own_meeting via keyboard_for_update); a freshly flushed instance has never loaded them
    # and User marks both lazy="raise", so load them explicitly.
    await session.refresh(new_user, ["joined_links", "meetups"])

    return new_user


async def handle_non_existing_user_join(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to join a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    user = await register_default_user(session, update)
    await user_joins_meeting(session, update, context, user, with_notification=False)
    await context.api.answer_callback_query(
        update=update,
        text=MeetingJoinMessages.JOIN_UNREGISTERED.get(),
        show_alert=True,
    )


@HandlersRegistry.register_callback_query(MeetingHandlerId.LEAVE, callback_data=cb.LEAVE)
@with_session(write=True)
async def leave_meetup(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the leave action when clicked on a meeting. This action can be clicked by any user
    who has already joined the meeting. If the user is not registered we should ask the user
    to register by opening a chat with the bot first.
    """
    try:
        user = await guards.current_user(update, session)
        await user_leaves_meeting(session, update, context, user)
    except UserNotFound:
        await handle_non_existing_user_leave(session, update, context)


async def user_leaves_meeting(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, with_notification: bool = True
):
    async def leave_operation(meeting: Meetup, user: User) -> MeetingJoinMessages:
        if joined_link := meeting.participant(user.db_id):
            promoted_links = meeting.remove_participant(joined_link)
            context.put_feature_metric(Feature.LEAVE_MEETING)

            await context.api.notify_users_promoted_from_waiting_list(
                joined_users=promoted_links,
                meeting=meeting,
            )

            return MeetingJoinMessages.LEAVE_SUCCESS

        return MeetingJoinMessages.LEAVE_NOT_JOINED

    await handle_join_leave_operation(session, update, context, user, leave_operation, with_notification)


async def handle_non_existing_user_leave(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to leave a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    user = await register_default_user(session, update)
    await user_leaves_meeting(session, update, context, user, with_notification=False)
    await context.api.answer_callback_query(
        update=update,
        text=MeetingJoinMessages.LEAVE_UNREGISTERED.get(),
        show_alert=True,
    )


async def handle_join_leave_operation(
    session: AsyncSession,
    update: Update,
    context: TMitupContext,
    user: User,
    operation: Callable[[Meetup, User], Awaitable[MeetingJoinMessages]],
    with_notification: bool = True,
):
    """Handle common infrastructure for meeting operations (join/leave)."""
    data = guards.valid_callback_data(cb.JOIN.parse(context.match), MeetingHandlerId.JOIN)
    # for_update: both operations read capacity/waiting-list state and mutate participants, so
    # the meetup row must be locked before the first read to serialize cross-user races.
    if meeting := await Meetup.by_id(session, data.id, for_update=True):
        # The locked load ran with populate_existing, which re-hydrates every entity its selectin
        # cascade touches — including `user` when they own or participate in this meeting —
        # resetting the lazy="raise" collections read below (own_meeting via keyboard_for_update,
        # joined_meeting in the operations) to unloaded. Re-load them; the row lock is already
        # held, so the re-read is race-safe.
        await session.refresh(user, ["meetups", "joined_links"])
        if not meeting.active:
            await context.api.edit_message(
                update=update,
                view=MitupView(
                    description=MeetingDisplayMessages.FINISHED_BANNER.get(lang=meeting.lang),
                    keyboard=[],
                ),
            )
            return

        if meeting.lock_on_start and meeting.is_in_progress:
            await context.api.answer_callback_query(
                update=update,
                text=MeetingJoinMessages.JOIN_LOCKED.get_text(lang=user.lang),
                show_alert=True,
            )
            return

        # Common message handling
        if (current_message := meeting.message_from_update(update)) is None:
            current_message = Message.from_update(
                update, meeting, meeting_views.keyboard_for_update(update, meeting, user)
            )
            meeting.messages.append(current_message)

        # Execute core operation
        notification_key = await operation(meeting, user)

        if with_notification:
            await context.api.answer_callback_query(
                update=update,
                text=notification_key.get(lang=user.lang),
                show_alert=False,
            )

        await context.api.update_meeting_messages(meeting=meeting, current_message=current_message)
    else:
        # The meeting was not found, update the message to inform the user
        # This should never happen because when the meeting is deleted all messages are updated
        await context.api.edit_message(update=update, view=MeetingDisplayMessages.DELETED_BANNER.get(lang=user.lang))
        context.emit_metric(MetricKey.STALE_MEETING_MESSAGE, include_handler_properties=False)
