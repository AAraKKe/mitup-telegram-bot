import logging

from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import CallbackQuery, Chat, Message, Update
from telegram.ext import ExtBot

from mitup_bot import api
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    UserNotFound,
)
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard, MitupView


def current_user(update: Update, session: Session) -> User:
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    # If we have an effective user, get the user from DB
    if user := User.by_tg_user_id(session, update.effective_user.id):
        return user
    else:
        raise UserNotFound(update.effective_user.id)


def chat(update: Update) -> Chat:
    if update.effective_chat is None:
        raise EffectiveChatNotSet(update)

    return update.effective_chat


def message(update: Update) -> Message:
    if update.effective_message is None:
        raise EffectiveMessageNotSet(update)

    return update.effective_message


def callback_query(update: Update) -> CallbackQuery:
    if update.callback_query is None:
        raise CallbackQueryNotSet(update)

    return update.callback_query


async def user_owns_meeting(
    user: User, meeting_id: int, action: str, update: Update, context: MitupContext[ExtBot, MetricsLogger]
) -> Meetup | None:
    """
    Check if the user owns the meeting.
    If the user does, the meeting is returned.
    If not, warn and send the user to the main menu and None is returned.
    """
    if meeting := user.own_meeting(meeting_id):
        context.put_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 0, unit=Unit.COUNT)
        return meeting

    message = (
        f"User tried {action!r} with a meeting that does not belong to them. "
        f"Meeting id: {meeting_id}, user id: {user.id}"
    )
    logging.warning(message)
    context.put_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 1, unit=Unit.COUNT)
    await api.edit_message(context, update, factory.main_menu_view())
    return None


async def meeting_accessible(
    session: Session,
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: MitupContext,
    custom_keyboard: Keyboard | None = None,
) -> Meetup | None:
    if Meetup.by_id(session, meeting_id):
        return await user_owns_meeting(user, meeting_id, action, update, context)

    message = (
        f"User tried {action!r} with a meeting that does not exist. " f"Meeting id: {meeting_id}, user id: {user.id}"
    )
    logging.warning(message)

    await api.edit_message(
        context,
        update,
        MitupView(
            description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(),
            keyboard=custom_keyboard
            or [[ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU)]],
        ),
    )
    return None
