import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import JoinedUsers, Meetup, User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

from .enums import EditMeetingHandlerId
from .views import edit_participants_view, kick_out_users_view


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
    callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS,
    bindable=True,
)
@with_async_session
async def edit_meeting_kickout_participants(session: Session, update: Update, context: TMitupContext):
    """
    Handle the kick out of a participant from a meeting. Once the user clicks in the kick out button,
    the next view shows a list of participants as buttons. The user selects the participant to be kicked out from
    the list and, after a confirmation message, the participant is removed from the meeting.
    """
    logging.debug("Enter into edit_meeting_kickout_participants")

    callback_data = guards.valid_meeting_callback_data(
        cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK
    )

    current_user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participants view",
        update,
        context,
    )

    if meeting is None:
        return

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
@with_async_session
async def edit_meeting_kickout_participant(session: Session, update: Update, context: TMitupContext):
    """
    This handler handles the action of kicking out a participant from a meeting after the user has selected
    the participant to be kicked out from the list of participants.

    Instead of immediately kicking out the participant, we show a confirmation message to the user.
    """
    logging.debug("Enter into edit_meeting_kickout_participant")

    callback_data = guards.valid_meeting_callback_data(
        cb.EDIT_MEETING_KICK_OUT_ACTION.parse(context.match), EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK
    )

    current_user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participant",
        update,
        context,
    )

    if meeting is None:
        return

    participant = meeting.participant(callback_data.id)
    if participant is None:
        await participant_no_longer_in_meeting(meeting, update, context, current_user)
        return

    participant_name = participant.user.inline_name
    confirmation_message = MeetingMessages.KICK_OUT_PARTICIPANT_CONFIRMATION_MESSAGE.get(
        lang=current_user.lang, participant=participant_name, meeting_title=meeting.title
    )
    confirmation_callback_data = cb.CONFIRM_KICK_OUT.with_ids(meeting_id=meeting.db_id, id=callback_data.id)
    decline_callback_data = cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(meeting_id=meeting.db_id, id=1)

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            lang=current_user.lang,
            message=confirmation_message,
            confirm_callback_data=confirmation_callback_data,
            # Cancel goes back to the list of participants to kick out
            decline_callback_data=decline_callback_data,
        ),
    )


async def participant_no_longer_in_meeting(meeting: Meetup, update: Update, context: TMitupContext, current_user: User):
    participant_no_longer_exists = MeetingMessages.PARTICIPANT_NO_LONGER_IN_MEETING.get(lang=current_user.lang)
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
@with_async_session
async def edit_meeting_kickout_participant_confirm(session: Session, update: Update, context: TMitupContext):
    logging.debug("Enter into edit_meeting_kickout_participant_confirm")

    callback_data = guards.valid_meeting_callback_data(
        cb.CONFIRM_KICK_OUT.parse(context.match),
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
    )

    current_user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        current_user,
        callback_data.meeting_id,
        "Kick out participant confirm",
        update,
        context,
    )

    if meeting is None:
        return

    participant = meeting.participant(callback_data.id)

    if participant is None:
        await participant_no_longer_in_meeting(meeting, update, context, current_user)
        return

    promoted_participants = meeting.remove_participant(participant)
    # If the participant is invited, remove it too
    if participant.invited_by is not None:
        session.delete(participant.user)
    session.flush()

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
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )

    context.put_feature_metric(Feature.KICK_OUT_PARTICIPANT)


async def kickout_user_to_edit_participants(
    meeting: Meetup, update: Update, context: TMitupContext, current_user: User, participant: JoinedUsers
):
    success_message = MeetingMessages.PARTICIPANT_KICKED_OUT_SUCCESS_NO_MORE_PARTICIPANTS.get(
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
    success_message = MeetingMessages.PARTICIPANT_KICKED_OUT_SUCCESS.get(
        lang=current_user.lang, participant=participant.user.inline_name
    )
    await context.api.edit_message(
        update=update,
        view=kick_out_users_view(meeting=meeting, current_user=current_user, page_number=1).with_context(
            success_message
        ),
    )
    return
