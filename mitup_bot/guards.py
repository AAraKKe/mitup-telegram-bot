from typing import cast

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import CallbackQuery, Chat, InlineQuery, Message, Update
from telegram import User as TgUser

from mitup_bot.callback_data import (
    CallbackData,
    DateCallbackData,
    MeetingCallbackData,
    PaginatedCallbackData,
    ValidCallbackData,
    ValidDateCallbackData,
    ValidMeetingCallbackData,
    ValidPaginatedCallbackData,
)
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
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MessageBase
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard, MitupView

log = structlog.get_logger(__name__)


async def member_user(update: Update, session: AsyncSession) -> User | None:
    """Return the effective user's `User` row only when their status is MEMBER, else None.

    Gates `/start` between the existing-member flow and the new/joined-only re-onboarding
    flow. JOINED_ONLY and LEFT users intentionally get None so they fall through to the
    re-onboarding conversation.
    """
    if update.effective_user is None:
        return None

    statement = select(User).where(
        User.tg_user_id == update.effective_user.id,
        User.status == UserStatus.MEMBER,
    )
    return (await session.exec(statement)).first()


async def current_user(update: Update, session: AsyncSession) -> User:
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    # If we have an effective user, get the user from DB
    if user := await User.by_tg_user_id(session, update.effective_user.id):
        return user
    else:
        raise UserNotFound(update.effective_user.id)


async def user_language(update: Update, session: AsyncSession) -> str:
    """Return the preferred language for the effective user, or the fallback language if unregistered."""
    if (tg_user := update.effective_user) and (user := await User.by_tg_user_id(session, tg_user.id)):
        return user.lang
    return TranslationEngine.FALLBACK_LANG


def valid_inline_query(update: Update) -> InlineQuery:
    if update.inline_query is None:
        raise InlineQueryNotSetError()
    return update.inline_query


async def shareable_meeting_id(update: Update, context: TMitupContext) -> int | None:
    """Return the meeting id from an inline share query, or `None` after answering with no results.

    PTB matches the inline pattern with `re.match` (not `fullmatch`), so a query such as "123abc"
    reaches the share handler even though it is not a valid meeting id. Answering with empty results
    here avoids letting `int()` raise and leaving the inline query silently unanswered.

    `isdecimal` (not `isdigit`) because `int()` rejects non-decimal digit characters such as "①",
    which `isdigit` accepts.
    """
    query = valid_inline_query(update).query.strip()
    if not query.isdecimal():
        await context.api.answer_inline_query(update=update, results=[], cache_time=0)
        return None
    return int(query)


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


def valid_paginated_callback_data(cb: PaginatedCallbackData, handler_id: HandlerId) -> ValidPaginatedCallbackData:
    """
    Validates the paginated callback `cb`. If an id cannot be set or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    A missing originating page defaults to the first page so handlers never re-derive it. The
    originating list stays None when absent: it means the detail was not reached from a list.

    The output of the guard is a `ValidPaginatedCallbackData`.
    """
    if cb.id is None or cb.unknown():
        raise MalformedCallbackData(handler_id, cb)
    return ValidPaginatedCallbackData(entity=cb.entity, action=cb.action, id=cb.id, page=cb.page or 1, source=cb.source)


def valid_meeting_callback_data(cb: MeetingCallbackData, handler_id: HandlerId) -> ValidMeetingCallbackData:
    """
    Validates the meeting callback `cb`. If an id cannot be set or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    The output of the guard is a `ValidMeetingCallbackData`.
    """
    if cb.id is None or cb.unknown() or cb.meeting_id is None:
        raise MalformedCallbackData(handler_id, cb)
    return ValidMeetingCallbackData(entity=cb.entity, action=cb.action, id=cb.id, meeting_id=cb.meeting_id)


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
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 0, unit=MetricUnit.COUNT)
        return meeting

    if redirect:
        message = (
            f"User tried {action!r} with a meeting that does not belong to them. "
            f"Meeting id: {meeting_id}, user id: {user.db_id}"
        )
        log.warning(message)
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 1, unit=MetricUnit.COUNT)
        await context.api.edit_message(update=update, view=factory.main_menu_view(lang=user.lang))
    return None


async def show_reactivation_prompt(
    user: User,
    meeting_id: int,
    update: Update,
    context: TMitupContext,
    custom_keyboard: Keyboard | None,
):
    """Edit the current message to show the reactivation prompt for an inactive meeting owned by the user."""
    await context.api.edit_message(
        update=update,
        view=factory.reactivation_prompt_view(
            lang=user.settings.language,
            meeting_id=meeting_id,
            back_rows=custom_keyboard,
        ),
    )


async def notify_meeting_removed(
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
    custom_keyboard: Keyboard | None,
):
    """Warn and edit the current message to inform the user the meeting no longer exists."""
    message = (
        f"User tried {action!r} with a meeting that does not exist. Meeting id: {meeting_id}, user id: {user.db_id}"
    )
    log.warning(message)

    await context.api.edit_message(
        update=update,
        view=MitupView(
            description=CommonMessages.DELETED_MEETING_ALERT.get(lang=user.settings.language),
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


async def meeting_accessible(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
    custom_keyboard: Keyboard | None = None,
) -> Meetup | None:
    """
    Check if the user has access to the meeting.
    If the user does, the meeting is returned.
    If not, warn and send the user to the main menu and None is returned.

    If the meeting does not exist, the user is warned that the meeting has been removed.

    If the meeting exists but is inactive, the owner is shown a reactivation prompt instead of
    the normal view. Non-owners fall through to the ownership check.

    If `custom_keyboard` is provided, it is used as the back-navigation row(s) in both the
    "meeting deleted" message and the reactivation prompt. Otherwise, a back button to the
    main menu is shown.

    **Note**: this method can only be used when a meeting is being accessed from the bot chat.
    """

    meeting = await Meetup.by_id(session, meeting_id)

    if meeting is None:
        await notify_meeting_removed(user, meeting_id, action, update, context, custom_keyboard)
        return None

    if not meeting.active and user.own_meeting(meeting_id):
        await show_reactivation_prompt(user, meeting_id, update, context, custom_keyboard)
        return None

    return await user_owns_meeting(user, meeting_id, action, update, context)


async def meeting_viewable(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
    custom_keyboard: Keyboard | None = None,
) -> Meetup | None:
    """Check whether the user may *view* the meeting, whether they own it or have only joined it.

    Unlike `meeting_accessible`, a non-owner who has joined an active meeting is allowed through so
    the caller can render the non-owner view (`Meetup.external_view`) instead of being bounced to the
    main menu. Can only be used when a meeting is accessed from the bot chat.
    """

    meeting = await Meetup.by_id(session, meeting_id)

    if meeting is None:
        await notify_meeting_removed(user, meeting_id, action, update, context, custom_keyboard)
        return None

    if not meeting.active:
        if user.own_meeting(meeting_id):
            await show_reactivation_prompt(user, meeting_id, update, context, custom_keyboard)
            return None
        return await user_owns_meeting(user, meeting_id, action, update, context)

    if user.own_meeting(meeting_id) or user.joined_meeting(meeting_id):
        return meeting

    return await user_owns_meeting(user, meeting_id, action, update, context)


async def user_registered(
    update: Update, session: AsyncSession, context: TMitupContext, alert_message: MessageBase
) -> User | None:
    """
    Context manager that yields the current user if they are subscribed to the bot.
    If the user is not subscribed, the callback query is answered with an allert showing the `alert_message`.
    """
    try:
        return await current_user(update, session)
    except UserNotFound as e:
        user = cast(TgUser, update.effective_user)  # We know the user exists here
        if update.callback_query is None:
            raise CallbackQueryNotSet(update) from e

        await context.api.answer_callback_query(
            update=update, text=alert_message.get(lang=user.language_code or "en"), show_alert=True
        )
