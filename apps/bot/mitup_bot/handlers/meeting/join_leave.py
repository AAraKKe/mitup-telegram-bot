from collections.abc import Awaitable, Callable

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.acquisition import SHARED_CARD_SOURCE
from mitup_bot.db import racy_flush, with_session
from mitup_bot.exceptions import EffectiveUserNotSet, UserNotFound
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message, User, utils
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.utils import MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views

from .enums import MeetingHandlerId
from .utils import log_waiting_list_promotions

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(MeetingHandlerId.JOIN, callback_data=cb.JOIN)
@with_session(write=True)
async def join_meetup(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the join action when clicked on a meeting. This action can be clicked by any user
    to whom the meeting has been shared to.

    If the user is not registered we should ask the user to register by opening a chat with the bot first.
    """
    try:
        # load_collections: the join checks the existing membership via `user.joined_meeting`, and
        # `keyboard_for_update` reads `user.own_meeting`.
        user = await guards.current_user(update, session, load_collections=True)
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
        def resolved(outcome: str, reason: str):
            """The one line that names how a tap on Join ended, and why."""
            log.info(
                "Meeting join resolved",
                joining_user_id=user.db_id,
                outcome=outcome,
                reason=reason,
                n_participants=meeting.n_participants,
                effective_max_members=meeting.effective_max_members,
                waiting_list_enabled=meeting.waiting_list,
            )

        if user.joined_meeting(meeting.db_id):
            resolved("already_joined", "already_member")
            return MeetingJoinMessages.JOIN_ALREADY_JOINED

        # Checked under the per-meeting row lock, so the answer cannot change before the
        # insert below — this is the only case where add_participant would produce nothing.
        if not meeting.join_allowed():
            resolved("refused", "meeting_full_no_waiting_list")
            return MeetingJoinMessages.JOIN_FULL

        joined_link = await racy_flush(
            session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
        )
        if joined_link is None:
            # A concurrent join already created the membership row; the unique constraint on
            # joined_users rejected our duplicate. Treat it as an idempotent no-op.
            resolved("duplicate_race", "unique_constraint_conflict")
            return MeetingJoinMessages.JOIN_ALREADY_JOINED

        context.put_feature_metric(Feature.JOIN_MEETING)
        if joined_link.is_waiting_list:
            resolved("waiting_list", "meeting_full_waiting_list_enabled")
            return MeetingJoinMessages.JOIN_FULL_WAITING_LIST
        resolved("joined", "ok")
        return MeetingJoinMessages.JOIN_SUCCESS

    await handle_join_leave_operation(session, update, context, user, join_operation, with_notification)


async def register_default_user(session: AsyncSession, update: Update) -> User:
    """
    Register the user with default values.
    """
    if update.effective_user is None:  # pragma: no cover
        raise EffectiveUserNotSet(update)

    new_user = utils.user_from_update(update, status=UserStatus.JOINED_ONLY, acquisition_source=SHARED_CARD_SOURCE)
    session.add(new_user)
    await session.flush()
    # The join/leave paths read the new user's collections right away (joined_meeting, and
    # own_meeting via keyboard_for_update); a freshly flushed instance has never loaded them
    # and User marks both lazy="raise", so load them explicitly.
    await session.refresh(new_user, ["joined_links", "meetups"])

    # Every JOINED_ONLY row is born here or in the invite flow, and this is the branch where the
    # account appears without the person ever having opened a chat with the bot.
    log.info(
        "User auto-registered from meeting card",
        user_id=new_user.db_id,
        status=new_user.status.value,
        reason="join_leave_from_shared_card",
    )

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
        text=MeetingJoinMessages.JOIN_UNREGISTERED.get(user=user.inline_name),
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
        # load_collections: `keyboard_for_update` reads `user.own_meeting`, and the shared
        # join/leave path re-loads both collections under the row lock.
        user = await guards.current_user(update, session, load_collections=True)
        await user_leaves_meeting(session, update, context, user)
    except UserNotFound:
        await handle_non_existing_user_leave(session, update, context)


async def user_leaves_meeting(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, with_notification: bool = True
):
    async def leave_operation(meeting: Meetup, user: User) -> MeetingJoinMessages:
        if joined_link := meeting.participant(user.db_id):
            was_waiting_list = joined_link.is_waiting_list
            promoted_links = meeting.remove_participant(joined_link)
            context.put_feature_metric(Feature.LEAVE_MEETING)

            log.info(
                "Meeting leave resolved",
                leaving_user_id=user.db_id,
                outcome="left",
                was_waiting_list=was_waiting_list,
                n_participants=meeting.n_participants,
                effective_max_members=meeting.effective_max_members,
            )
            log_waiting_list_promotions(meeting, promoted_links, reason="participant_left")

            await context.api.notify_users_promoted_from_waiting_list(
                joined_users=promoted_links,
                meeting=meeting,
            )

            return MeetingJoinMessages.LEAVE_SUCCESS

        log.info(
            "Meeting leave resolved",
            leaving_user_id=user.db_id,
            outcome="not_joined",
            was_waiting_list=False,
            n_participants=meeting.n_participants,
            effective_max_members=meeting.effective_max_members,
        )
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
        text=MeetingJoinMessages.LEAVE_UNREGISTERED.get(user=user.inline_name),
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
    # lock: both operations read capacity/waiting-list state and mutate participants, so the meetup
    # row must be locked before the first read to serialize cross-user races.
    meeting = await guards.shared_meeting(session, user, data.id, "join or leave a meeting", update, lock=True)

    # The guard's locked load ran with populate_existing, which re-hydrates every entity its selectin
    # cascade touches — including `user` when they own or participate in this meeting — resetting the
    # lazy="raise" collections read below (own_meeting via keyboard_for_update, joined_meeting in the
    # operations) to unloaded. Re-load them; the row lock is already held, so the re-read is race-safe.
    await session.refresh(user, ["meetups", "joined_links"])

    if meeting.lock_on_start and meeting.is_in_progress:
        # "Why couldn't I join?" is the domain's most-asked support question, and the state that
        # produced the refusal has to be on the line: `is_in_progress` is a function of the clock,
        # so by the time anyone reads this it can no longer be re-derived.
        log.info(
            "Meeting interaction refused",
            user_id=user.db_id,
            reason="locked_on_start_in_progress",
            meeting_datetime=meeting.datetime,
            meeting_end_datetime=meeting.end_datetime,
            lock_on_start=meeting.lock_on_start,
        )
        await context.api.answer_callback_query(
            update=update,
            text=MeetingJoinMessages.JOIN_LOCKED.get_text(lang=user.lang),
            show_alert=True,
        )
        return

    # Common message handling
    if (current_message := meeting.message_from_update(update)) is None:
        current_message = Message.from_update(update, meeting, meeting_views.keyboard_for_update(update, meeting, user))
        meeting.messages.append(current_message)
        # The stored row decides which surfaces the later fan-out reaches, so its creation is a
        # decision rather than bookkeeping: an unreachable-message warning weeks later points back
        # at this line for where the card was first seen.
        log.info(
            "Meeting message linked",
            user_id=user.db_id,
            message_source="inline" if current_message.inline_message_id is not None else "callback_message",
            chat_instance_set=current_message.chat_instance is not None,
            reason="first_interaction_from_this_message",
        )
    else:
        current_message.capture_chat_instance(update)

    # Execute core operation
    notification_key = await operation(meeting, user)

    if with_notification:
        await context.api.answer_callback_query(
            update=update,
            text=notification_key.get(lang=user.lang),
            show_alert=False,
        )

    await context.api.update_meeting_messages(meeting=meeting, current_message=current_message)
