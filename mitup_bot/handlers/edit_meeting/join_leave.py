import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.api import update_meeting_messages
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import UserNotFound
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import JoinedUsers, Meetup, Message
from mitup_bot.monitoring import Feature
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.types import TMitupContext

from .enums import EditMeetingHandlerId


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.JOIN, callback_data=cb.JOIN)
@with_async_session
async def join_meetup(session: Session, update: Update, context: TMitupContext):
    """
    Handle the join action when clicked on a meeting. This action can be clicked by any user
    to whom the meeting has be shared to. If the user is not registered we should ask the user
    to register by opening a chat with the bot first
    TODO: For now, we are assuming that the user is already registered and we are directly
    handling this case since we need to have this verison working for the TFG demo. We cannot
    close the story without this feature fully implemented.
    """
    try:
        user = guards.current_user(update, session)
        data = guards.valid_callback_data(cb.JOIN.parse(context.match), EditMeetingHandlerId.JOIN)
        if meeting := Meetup.by_id(session, data.id):
            # Register the meeting the button was clicked from to be able to edit it later
            logging.info(f"Joining meeting {meeting}")
            logging.info(f"Messages: {meeting.messages}")
            if not meeting.has_message(update):
                session.add(Message.from_update(update, meeting, user))

            # Only join if the user is not already joined
            if not user.joined_meeting(data.id):
                meeting.joined_links.append(JoinedUsers(user=user, meetup=meeting))
                session.add(meeting)
                context.put_feature_metric(Feature.JOIN_MEETING)

            session.flush()
            session.refresh(meeting)
            # Edit now all messages where the meeting has been shared
            await update_meeting_messages(session, context, meeting)
    except UserNotFound:
        join_non_registered_user(session, update, context)


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
        data = guards.valid_callback_data(cb.JOIN.parse(context.match), EditMeetingHandlerId.JOIN)
        if meeting := Meetup.by_id(session, data.id):
            # Register the message the button was clicked from to be able to edit it later
            current_message = meeting.add_message(update, user)

            # Only leave if the user is already joined
            if joined_link := user.joined_meeting(data.id):
                session.delete(joined_link)
                context.put_feature_metric(Feature.LEAVE_MEETING)

            session.flush()
            session.refresh(meeting)
            # Edit now all messages where the meeting has been shared
            await update_meeting_messages(session, context, meeting, current_message=current_message)
    except UserNotFound:
        leave_non_registered_user(session, update, context)


def join_non_registered_user(session: Session, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to join a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    pass


def leave_non_registered_user(session: Session, update: Update, context: TMitupContext):
    """
    Handle the case when a user tries to leave a meeting but is not registered with the bot.
    We should ask the user to open a chat with the bot first to register.
    """
    pass
