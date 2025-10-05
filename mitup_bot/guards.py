import logging

from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import CallbackQuery, Chat, InlineQuery, Message, Update

from mitup_bot import api
from mitup_bot.callback_data import (
    CallbackData,
    DateCallbackData,
    KickoutCallbackData,
    ValidCallbackData,
    ValidDateCallbackData,
    ValidKickoutCallbackData,
)
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    InlineQueryNotSetError,
    MalformedCallbackData,
    UserNotFound,
)
from mitup_bot.handler_id import HandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.mitup_types import TMitupContext
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


def valid_inline_query(update: Update) -> InlineQuery:
    if update.inline_query is None:
        raise InlineQueryNotSetError()
    return update.inline_query


def valid_callback_query(update: Update) -> CallbackQuery:
    if update.callback_query is None:
        raise CallbackQueryNotSet(update)
    return update.callback_query


def valid_date_callback_data(cb: DateCallbackData, handler_id: HandlerId) -> ValidDateCallbackData:
    """
    Validates the callback `cb`. If an id or date cannot be set or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    The output of the guard is a `ValidDateCallbackData`.
    """
    if cb.id is None or cb.date is None or cb.unknown():
        raise MalformedCallbackData(handler_id, cb)
    return ValidDateCallbackData(entity=cb.entity, action=cb.action, id=cb.id, date=cb.date)


def valid_callback_data(cb: CallbackData, handler_id: HandlerId) -> ValidCallbackData:
    """
    Validates the callback `cb`. If an id cannot be set or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    The output of the guard is a `ValidCallbackData`.
    """
    if cb.id is None or cb.unknown():
        raise MalformedCallbackData(handler_id, cb)
    return ValidCallbackData(entity=cb.entity, action=cb.action, id=cb.id)


def valid_kickout_callback_data(cb: KickoutCallbackData, handler_id: HandlerId) -> ValidKickoutCallbackData:
    """
    Validates the kickout callback `cb`. If an id cannot be set or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    The output of the guard is a `ValidKickoutCallbackData`.
    """
    if cb.id is None or cb.unknown() or cb.meeting_id is None:
        raise MalformedCallbackData(handler_id, cb)
    return ValidKickoutCallbackData(entity=cb.entity, action=cb.action, id=cb.id, meeting_id=cb.meeting_id)


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
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
    redirect=True,
) -> Meetup | None:
    """
    Check if the user owns the meeting.
    If the user does, the meeting is returned.
    If not, if the redirect flag is set to True, warn and send the user to the main menu and None is returned.
    If the redirect flag is False, None is returned but no communication happens with the user.
    """
    if meeting := user.own_meeting(meeting_id):
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 0, unit=Unit.COUNT)
        return meeting

    if redirect:
        message = (
            f"User tried {action!r} with a meeting that does not belong to them. "
            f"Meeting id: {meeting_id}, user id: {user.db_id}"
        )
        logging.warning(message)
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 1, unit=Unit.COUNT)
        await api.edit_message(context=context, update=update, view=factory.main_menu_view(lang=user.lang))
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
    """
    Check if the user has access to the meeting.
    If the user does, the meeting is returned.
    If not, warn and send the user to the main menu and None is returned.

    If the meeting does not exist, the user is warned that the meeting has been removed.

    If `custom_keyboard` is provided, it is attached to the message shown the user. Otherwise, a
    a keyboard with a back button to the main menu is shown.

    **Note**: this method can only be used when a meeting is being accessed from the bot chat.
    """

    if Meetup.by_id(session, meeting_id):
        return await user_owns_meeting(user, meeting_id, action, update, context)

    message = (
        f"User tried {action!r} with a meeting that does not exist. Meeting id: {meeting_id}, user id: {user.db_id}"
    )
    logging.warning(message)

    await api.edit_message(
        context=context,
        update=update,
        view=MitupView(
            description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(lang=user.settings.language),
            keyboard=custom_keyboard
            or [
                [
                    ButtonConfig(
                        text=f"{ButtonMessages.MAIN_MENU.back(lang=user.settings.language)}",
                        callback_data=cb.MAIN_MENU,
                    )
                ]
            ],
        ),
    )
    return None
