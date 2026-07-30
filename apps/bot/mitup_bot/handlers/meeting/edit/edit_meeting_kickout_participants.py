import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import JoinedUsers, Meetup, User
from mitup_bot.utils import MeetingEditParticipantsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from mitup_bot.views.meeting_text import rich_title

from ..utils import log_waiting_list_promotions
from .enums import EditMeetingHandlerId
from .views import edit_participants_view, kick_out_users_view

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
    callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS,
    bindable=True,
)
@with_session
async def edit_meeting_kickout_participants(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle the kick out of a participant from a meeting. Once the user clicks in the kick out button,
    the next view shows a list of participants as buttons. The user selects the participant to be kicked out from
    the list and, after a confirmation message, the participant is removed from the meeting.
    """
    callback_data = guards.valid_meeting_callback_data(
        cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK
    )

    current_user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participants view",
        context,
    )

    participants = [participant for participant in meeting.participants if participant.user.db_id != current_user.db_id]

    if not participants:
        await context.api.edit_message(update=update, view=edit_participants_view(meeting))
        return

    # Use a paginated view in case there are many participants so it does not turn into
    # an unusable list of buttons
    view = kick_out_users_view(
        page_number=callback_data.id,
        meeting=meeting,
        current_user=current_user,
    )

    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK,
    callback_data=cb.EDIT_MEETING_KICK_OUT_ACTION,
    bindable=True,
)
@with_session
async def edit_meeting_kickout_participant(session: AsyncSession, update: Update, context: TMitupContext):
    """
    This handler handles the action of kicking out a participant from a meeting after the user has selected
    the participant to be kicked out from the list of participants.

    Instead of immediately kicking out the participant, we show a confirmation message to the user.
    """
    callback_data = guards.valid_meeting_callback_data(
        cb.EDIT_MEETING_KICK_OUT_ACTION.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK
    )

    current_user = await guards.current_user(update, session)

    meeting = await guards.meeting(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participant",
        context,
    )

    participant = meeting.participant(callback_data.id)
    if participant is None:
        await participant_no_longer_in_meeting(
            meeting, update, context, current_user, target_user_id=callback_data.id, step="select"
        )
        return

    participant_name = participant.user.inline_name
    confirmation_message = MeetingEditParticipantsMessages.KICK_OUT_CONFIRMATION.get(
        lang=current_user.lang, participant=participant_name, meeting_title=rich_title(meeting)
    )
    confirmation_callback_data = cb.CONFIRM_KICK_OUT.with_ids(meeting_id=meeting.db_id, id=callback_data.id)
    decline_callback_data = cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(meeting_id=meeting.db_id, id=1)

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            guards.render_context(current_user, update, context),
            message=confirmation_message,
            confirm_callback_data=confirmation_callback_data,
            # Cancel goes back to the list of participants to kick out
            decline_callback_data=decline_callback_data,
        ),
    )


async def participant_no_longer_in_meeting(
    meeting: Meetup, update: Update, context: TMitupContext, current_user: User, target_user_id: int, step: str
):
    """Tell the owner the target is gone, saying at which point in the kick-out flow they found out.

    `select` means the list they tapped was drawn before the participant left; `confirm` means the
    participant left between the confirmation prompt and the owner's answer. The two look identical
    on screen and mean different things.
    """
    log.info(
        "Kick out target no longer a participant",
        user_id=current_user.db_id,
        target_user_id=target_user_id,
        reason="participant_left_before_confirm",
        step=step,
    )
    participant_no_longer_exists = MeetingEditParticipantsMessages.KICK_OUT_NOT_IN_MEETING.get(lang=current_user.lang)
    await context.api.edit_message(
        update=update,
        view=edit_participants_view(meeting).with_context(participant_no_longer_exists),
    )
    return


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
    callback_data=cb.CONFIRM_KICK_OUT,
    bindable=True,
)
@with_session(write=True)
async def edit_meeting_kickout_participant_confirm(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_meeting_callback_data(
        cb.CONFIRM_KICK_OUT.parse(context.match),
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
    )

    current_user = await guards.current_user(update, session)

    # lock: removing a participant can promote from the waiting list, so the membership
    # read and the removal must happen under the per-meeting row lock. The earlier list and
    # confirmation steps are read-only and deliberately stay unlocked.
    meeting = await guards.meeting(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participant confirm",
        context,
        lock=True,
    )

    participant = meeting.participant(callback_data.id)

    if participant is None:
        await participant_no_longer_in_meeting(
            meeting, update, context, current_user, target_user_id=callback_data.id, step="confirm"
        )
        return

    # Recorded before the fan-out below, which can fail: after it, a partial delivery would read as
    # a completed kick-out. `was_invited` is read here because the link is about to be detached.
    log.info(
        "Participant kicked out",
        user_id=current_user.db_id,
        participant_user_id=participant.user.id,
        participant_tg_user_id=participant.user.tg_user_id,
        was_invited=participant.invited_by is not None,
        was_waiting_list=participant.is_waiting_list,
        reason="owner_kicked_out",
    )

    promoted_participants = meeting.remove_participant(participant)
    log_waiting_list_promotions(meeting, promoted_participants, reason="participant_kicked_out")

    # If the participant is invited, remove it too
    if participant.invited_by is not None:
        # A placeholder User row is destroyed here, not just a membership. Nothing else records
        # that this account existed, so the line names it and the inviter it belonged to.
        log.info(
            "Invited user deleted",
            user_id=current_user.db_id,
            deleted_user_id=participant.user.id,
            invited_by_user_id=participant.invited_by.id,
            reason="invited_participant_kicked_out",
        )
        await session.delete(participant.user)

    # We need to decide whetehr we go back to the edit participatns view or the list of participants to kick out
    # If there are no more participants to kick out, we go back to the edit participants view
    available_to_kickout = [
        participant for participant in meeting.participants if participant.user.db_id != current_user.db_id
    ]

    if available_to_kickout:
        await kickout_user_to_kickout_participants(meeting, update, context, current_user, participant)
    else:
        await kickout_user_to_edit_participants(meeting, update, context, current_user, participant)

    # Send messages to any user that has been promoted from the waiting list
    await context.api.notify_users_promoted_from_waiting_list(
        joined_users=promoted_participants,
        meeting=meeting,
    )

    # After all has been taken care of, we need to update all messages for the meeting
    # Avoid editing current message since we have done that already
    await context.api.update_meeting_messages(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


async def kickout_user_to_edit_participants(
    meeting: Meetup, update: Update, context: TMitupContext, current_user: User, participant: JoinedUsers
):
    success_message = MeetingEditParticipantsMessages.KICK_OUT_SUCCESS_NO_MORE.get(
        lang=current_user.lang, participant=participant.user.inline_name
    )
    await context.api.edit_message(
        update=update,
        view=edit_participants_view(meeting).with_context(success_message),
    )
    return


async def kickout_user_to_kickout_participants(
    meeting: Meetup, update: Update, context: TMitupContext, current_user: User, participant: JoinedUsers
):
    success_message = MeetingEditParticipantsMessages.KICK_OUT_SUCCESS.get(
        lang=current_user.lang, participant=participant.user.inline_name
    )
    await context.api.edit_message(
        update=update,
        view=kick_out_users_view(meeting=meeting, current_user=current_user, page_number=1).with_context(
            success_message
        ),
    )
    return
