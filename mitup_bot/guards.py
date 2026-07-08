from typing import cast

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import CallbackQuery, Chat, InlineQuery, Message, Update
from telegram import User as TgUser

from mitup_bot import supporter
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
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MessageBase, MessageParams
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


def is_admin(update: Update, context: TMitupContext) -> bool:
    """Return whether the effective user is a bot admin.

    The single admin predicate for the whole bot: pure and synchronous (no DB), True iff there is
    an effective user whose Telegram id is on the `admin_tg_ids` allowlist from `context.bot_config`.
    An empty allowlist keeps every admin-gated surface dormant.
    """
    return update.effective_user is not None and update.effective_user.id in context.bot_config.admin_tg_ids


async def current_user(update: Update, session: AsyncSession, *, load_collections: bool = True) -> User:
    # `load_collections` forwards to `User.by_tg_user_id`: handlers that never traverse the user's
    # meetups/joined_links (settings-only screens, the Collaborate menu, etc.) pass False at their
    # entry point to skip the two selectin queries. It stays True by default so opting out is always
    # a deliberate, audited per-call-site decision.
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    # If we have an effective user, get the user from DB
    if user := await User.by_tg_user_id(session, update.effective_user.id, load_collections=load_collections):
        return user
    else:
        raise UserNotFound(update.effective_user.id)


async def user_language(update: Update, session: AsyncSession) -> str:
    """Return the preferred language for the effective user, or the fallback language if unregistered."""
    # Reads only `user.lang` (a Settings-backed column), never the meetups/joined_links collections,
    # so skip loading them: this guard runs on every inline-query keystroke.
    if (tg_user := update.effective_user) and (
        user := await User.by_tg_user_id(session, tg_user.id, load_collections=False)
    ):
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
        await context.api.edit_message(
            update=update, view=factory.main_menu_view(lang=user.lang, is_admin=is_admin(update, context))
        )
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
    for_update: bool = False,
) -> Meetup | None:
    """
    Return the meeting when the user may act on it as its owner (bot-chat access only); otherwise
    inform the user of the failing case (removed, inactive-but-owned → reactivation prompt, not
    owned → main-menu redirect) and return None, on which callers must bail immediately.

    Participant- or capacity-mutating callers must pass `for_update=True` so the meetup row (the
    per-meeting mutex) is locked before any capacity/waiting-list read. Parameter mechanics such
    as `custom_keyboard` are documented in the guards skill.
    """

    meeting = await Meetup.by_id(session, meeting_id, for_update=for_update)

    if meeting is None:
        await notify_meeting_removed(user, meeting_id, action, update, context, custom_keyboard)
        return None

    if for_update:
        # The locked load ran with populate_existing, which re-hydrates every entity its selectin
        # cascade touches — including `user` when they own or participate in the meeting —
        # resetting the lazy="raise" collections the ownership checks below traverse. Re-load
        # them; the row lock is already held, so the re-read is race-safe.
        await session.refresh(user, ["meetups", "joined_links"])

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


async def supporter_required(
    user: User,
    update: Update,
    context: TMitupContext,
    alert_message: MessageBase,
    *,
    minimum: SupporterLevel,
    **message_kwargs: MessageParams,
) -> User | None:
    """Return the user when their tier reaches `minimum`; otherwise answer the callback query with an
    alert and return None, on which the caller must bail immediately.

    Call-and-check, mirroring `user_registered`. The decision is resolved through the supporter-tier
    policy (`supporter.meets`) rather than comparing levels here; `supporter_level` is a plain column
    kept in sync by the recurring job and the OAuth callback, so this never queries Patreon inline.
    `alert_message` is supplied per feature and is expected to point the user at the Collaborate menu
    entry; any `${...}` placeholders it carries are filled from `message_kwargs`. It is rendered as
    plain text, so it must carry no inline-formatting entities.
    """
    if supporter.meets(user.supporter_level, minimum):
        return user

    await context.api.answer_callback_query(
        update=update, text=alert_message.get_text(lang=user.lang, **message_kwargs), show_alert=True
    )
    return None


async def user_registered(
    update: Update,
    session: AsyncSession,
    context: TMitupContext,
    alert_message: MessageBase,
    *,
    load_collections: bool = True,
) -> User | None:
    """
    Context manager that yields the current user if they are subscribed to the bot.
    If the user is not subscribed, the callback query is answered with an allert showing the `alert_message`.

    `load_collections` forwards to `current_user`; leave it True unless the caller has verified it
    never traverses the user's meetups/joined_links (see `current_user`).
    """
    try:
        return await current_user(update, session, load_collections=load_collections)
    except UserNotFound as e:
        user = cast(TgUser, update.effective_user)  # We know the user exists here
        if update.callback_query is None:
            raise CallbackQueryNotSet(update) from e

        await context.api.answer_callback_query(
            update=update, text=alert_message.get(lang=user.language_code or "en"), show_alert=True
        )
