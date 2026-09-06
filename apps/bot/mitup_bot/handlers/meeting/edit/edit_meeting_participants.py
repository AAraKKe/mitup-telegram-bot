from typing import cast

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, limits
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.exceptions import ContextPropertyNotSetError
from mitup_bot.handlers.meeting.utils import participant_capacity_rejection
from mitup_bot.handlers.personal_filters import PositiveNumberFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import recover_from_lost_context
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import CommonMessages, MeetingEditParticipantsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import collaborate_button

from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import log_stale_navigation
from .views import edit_max_participants_view

log = structlog.get_logger(__name__)

# The `action` facet the remove-limit confirmation lines carry.
REMOVE_LIMIT_ACTION = "remove_limit"


def log_capacity_change(meeting: Meetup, user: User, old_max_members: int | None, reason: str):
    """Record a capacity write, with the state it decides.

    `max_members` is not the cap that applies: a capped owner clearing the limit falls back to their
    plan's, so the effective value is on the line beside the raw one. Capacity governs `join_allowed`,
    the joined-versus-waiting split and every promotion, so the counts it is now judged against
    belong on the same record.
    """
    log.info(
        "Meeting capacity changed",
        user_id=user.db_id,
        old_max_members=old_max_members,
        new_max_members=meeting.max_members,
        effective_max_members=meeting.effective_max_members,
        n_participants=meeting.n_participants,
        n_waiting=meeting.n_waiting,
        reason=reason,
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CALLBACK, callback_data=cb.EDIT_MEETING_PARTICIPANTS, bindable=True
)
@with_session
async def callback_edit_meeting_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit participants",
        context,
    )

    # No current screen renders this button; it survives only on old messages, so the tap lands
    # on the editor card, which owns the limit and kick-out controls.
    log_stale_navigation(user, "edit_meeting_participants")
    await context.api.edit_message(
        update=update,
        view=meeting_views.owner_view(meeting).with_context(CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user.lang)),
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK, callback_data=cb.EDIT_MEETING_MAX_PARTICIPANTS, bindable=False
)
@with_session
async def callback_edit_meeting_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_MAX_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK
    )

    user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit max participants",
        context,
    )

    context.store_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_MAX_PARTICIPANTS,
        MeetingEditParticipantsMessages.MAX_ON_EXIT,
        cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(callback_data.id),
        lang=user.lang,
    )

    await context.api.edit_message(update=update, view=edit_max_participants_view(meeting))

    return ConversationMeetingState.EDIT_MAX_PARTICIPANTS


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
    callback_data=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS,
    bindable=False,
)
@with_session(write=True)
async def callback_edit_meeting_no_limit_participants(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK
    )
    user = await guards.current_user(update, session)

    # lock: capacity changes race with concurrent joins reading `full`, so the write happens under
    # the per-meeting row lock.
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Edit no limit participants",
        context,
        lock=True,
    )

    log_stale_navigation(user, "edit_meeting_no_limit_participants")
    old_max_members = meeting.max_members
    meeting.max_members = None
    log_capacity_change(meeting, user, old_max_members, reason="cleared_to_plan_default")

    # A capped owner's cleared limit resolves to the plan's cap (Meetup.effective_max_members),
    # so the banner states that number rather than claiming no limit: the value the owner sees on
    # the card is not the "no limit" they asked for.
    cap = limits.participant_capacity(meeting.owner)
    limit_text = MeetingEditParticipantsMessages.NO_LIMIT_LABEL.rich(lang=user.lang) if cap is None else str(cap)
    response_view = meeting_views.owner_view(meeting).with_context(
        MeetingEditParticipantsMessages.MAX_SUCCESS.rich(max_participants=limit_text)
    )

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "max_participants"})
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS,
    bindable=False,
)
@with_session
async def callback_cancel_edit_meeting_participants(session: AsyncSession, update: Update, context: TMitupContext):
    context.clean_all_user_data(reason="participants_edit_cancelled")

    callback_data = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_PARTICIPANS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session,
        user,
        callback_data.id,
        "Cancel edit participants",
        context,
        access=guards.MeetingAccess.OWNER_ANY_STATE,
    )

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))

    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE, filters=PositiveNumberFilter(), bindable=False
)
@with_session(write=True)
async def edit_meeting_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    number = guards.message(update).text
    user = await guards.current_user(update, session)

    try:
        # The stored meeting id is read without consuming it: a refused number leaves the owner in
        # this same state to answer with another one, and that retry needs the id still there. The
        # end of the flow clears it below.
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, ensure_clean=False) as meeting_id:
            # lock: capacity changes race with concurrent joins reading `full`, so the meeting is
            # resolved and written under the per-meeting row lock.
            meeting = await guards.meeting(
                session,
                user,
                meeting_id,
                "Edit max participants",
                context,
                access=guards.MeetingAccess.OWNER_ANY_STATE,
                lock=True,
            )
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(
            session, update, context, user, exc, ContextId.EDIT_MEETING_MAX_PARTICIPANTS
        )

    requested_max = int(cast(str, number))

    # A free owner is capped by the free-tier participant limit; reject anything above it and keep
    # the conversation open so they can enter a valid number. Supporter owners set any limit. The
    # rejection carries the Collaborate button inline: a raised capacity is exactly what
    # Collaborate offers.
    if rejection := participant_capacity_rejection(user, requested_max):
        view = edit_max_participants_view(meeting)
        await context.api.send_message(update=update, view=view.with_context(rejection))
        return ConversationMeetingState.EDIT_MAX_PARTICIPANTS

    old_max_members = meeting.max_members
    meeting.max_members = requested_max
    log_capacity_change(meeting, user, old_max_members, reason="owner_set_limit")

    await context.api.send_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(meeting=meeting)

    context.clean_user_data([ContextId.EDIT_MEETING_MAX_PARTICIPANTS])
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "max_participants"})
    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE, filters=~PositiveNumberFilter(), bindable=False
)
@with_session
async def edit_meeting_wrong_max_participants(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    try:
        with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, ensure_clean=False) as meeting_id:
            meeting = await guards.meeting(
                session,
                user,
                meeting_id,
                "Edit max participants",
                context,
                access=guards.MeetingAccess.OWNER_ANY_STATE,
            )
            # This view reads only `meeting.lang` (the acting owner's language), never the
            # participant list.
            response_view = edit_max_participants_view(meeting, fail=True)
    except ContextPropertyNotSetError as exc:
        return await recover_from_lost_context(
            session, update, context, user, exc, ContextId.EDIT_MEETING_MAX_PARTICIPANTS
        )

    await context.api.send_message(update=update, view=response_view)

    return ConversationMeetingState.EDIT_MAX_PARTICIPANTS


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK],
    states={
        ConversationMeetingState.EDIT_MAX_PARTICIPANTS: [
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
            EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
            EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE],
)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CALLBACK, callback_data=cb.DELETE_MEETING_LIMIT
)
@with_session(write=True)
async def callback_query_remove_limit(session: AsyncSession, update: Update, context: TMitupContext):
    """Remove the participant limit from the editor card.

    For an uncapped owner the limit truly goes away, so it clears outright. A capped owner's
    removed limit resolves to the plan's cap instead, so they get a confirmation stating that
    number first, with Collaborate beside it: a raised capacity is exactly what Collaborate offers.
    """
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_LIMIT.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CALLBACK
    )
    user = await guards.current_user(update, session)

    cap = limits.participant_capacity(user)
    if cap is None:
        # lock: capacity changes race with concurrent joins reading `full`, so the write happens
        # under the per-meeting row lock.
        meeting = await guards.meeting(session, user, callback_data.id, "remove_limit", context, lock=True)

        old_max_members = meeting.max_members
        meeting.max_members = None
        log_capacity_change(meeting, user, old_max_members, reason="owner_removed_limit")

        await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
        await context.api.update_meeting_messages(
            meeting=meeting,
            current_message=meeting.message_from_update(update),
            skip_current=True,
        )
        context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "max_participants"})
        return

    await guards.meeting(session, user, callback_data.id, "remove_limit", context)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=REMOVE_LIMIT_ACTION)

    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=MeetingEditParticipantsMessages.REMOVE_LIMIT_CONFIRMATION.rich(
            lang=user.lang, cap=cap, button_collaborate=collaborate_button(user.lang)
        ),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_LIMIT.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_LIMIT.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CONFIRM_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_LIMIT
)
@with_session(write=True)
async def callback_query_confirm_remove_limit(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_LIMIT.parse(context.match),
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CONFIRM_CALLBACK,
    )
    user = await guards.current_user(update, session)

    # lock: capacity changes race with concurrent joins reading `full`, so the write happens under
    # the per-meeting row lock.
    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_limit", context, lock=True)

    old_max_members = meeting.max_members
    meeting.max_members = None
    log_capacity_change(meeting, user, old_max_members, reason="cleared_to_plan_default")

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "max_participants"})


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_DECLINE_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_LIMIT
)
@with_session
async def callback_query_decline_remove_limit(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_LIMIT.parse(context.match),
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_DECLINE_CALLBACK,
    )
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_limit", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_LIMIT_ACTION)

    await context.api.edit_message(update=update, view=meeting_views.owner_view(meeting))
