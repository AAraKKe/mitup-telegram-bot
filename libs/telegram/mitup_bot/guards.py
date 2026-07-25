from enum import Enum, auto
from typing import cast

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import CallbackQuery, Chat, ChosenInlineResult, InlineQuery, Message, Update
from telegram import User as TgUser

from mitup_bot.callback_data import (
    CallbackData,
    CodeCallbackData,
    DateCallbackData,
    MeetingCallbackData,
    PaginatedCallbackData,
    ValidCallbackData,
    ValidCodeCallbackData,
    ValidDateCallbackData,
    ValidMeetingCallbackData,
    ValidPaginatedCallbackData,
)
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    ChosenInlineResultNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    InlineQueryNotSetError,
    MalformedCallbackData,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.handler_id import HandlerId
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingDisplayMessages, MessageBase
from mitup_bot.views import RenderContext, factory
from mitup_bot.views.mitup_view import MitupView

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


def render_context(user: User, update: Update, context: TMitupContext) -> RenderContext:
    """Build the `RenderContext` for the acting user from the current update.

    The one place handlers turn PTB objects into the display context every view factory takes:
    the user's language plus whether they are a bot admin (via `is_admin`). Build it once per
    handler and pass it to each factory call; use `RenderContext.with_lang` for the rare screen
    that must render in a language other than the acting user's.
    """
    return RenderContext(lang=user.lang, is_admin=is_admin(update, context))


async def current_user(update: Update, session: AsyncSession, *, load_collections: bool = False) -> User:
    # `load_collections` forwards to `User.by_tg_user_id`. The default loads no collections: the vast
    # majority of screens act on a single meeting they resolve through `guards.meeting`, so paying for
    # the user's meetups/joined_links everywhere would be waste. The screens that do traverse them
    # (the meeting lists, and the paths reading `own_meeting`/`joined_meeting`) pass
    # `load_collections=True` and say at the call site what reads them. Both collections are
    # `lazy="raise"`, so a missing opt-in surfaces as an `InvalidRequestError` rather than silent I/O.
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    # If we have an effective user, get the user from DB
    if user := await User.by_tg_user_id(session, update.effective_user.id, load_collections=load_collections):
        # A user marked for deletion is rejected everywhere until the cleanup run purges the row;
        # the error handler answers the interaction with the standardized pending-deletion alert.
        if user.status is UserStatus.DELETION_REQUESTED:
            raise UserPendingDeletion(user.tg_user_id, user.lang)
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


def valid_code_callback_data(cb: CodeCallbackData, handler_id: HandlerId) -> ValidCodeCallbackData:
    """
    Validates the code-addressed callback `cb`. If the code is missing or the entity is unknown,
    a MalformedCallbackData exception is raised scoped to the `handler_id` provided.

    Holding a well-formed code proves nothing on its own — the handler still has to match it against
    a row that the pressing user already claimed. This guard only rules out a callback there is
    nothing to look up from.

    The output of the guard is a `ValidCodeCallbackData`.
    """
    if cb.is_malformed():
        raise MalformedCallbackData(handler_id, cb)
    assert cb.code is not None, "is_malformed guarantees the code is set"
    return ValidCodeCallbackData(entity=cb.entity, action=cb.action, code=cb.code)


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


def chosen_inline_result(update: Update) -> ChosenInlineResult:
    if update.chosen_inline_result is None:
        raise ChosenInlineResultNotSet(update)

    return update.chosen_inline_result


class MeetingAccess(Enum):
    """Which callers `meeting` lets through, and how it answers the ones it stops.

    `OWNER` and `OWNER_OR_JOINED` are the live-meeting profiles: a meeting that no longer exists
    gets the deleted-meeting notice, and an inactive one gets the reactivation prompt for its owner
    and the main-menu redirect for everybody else. `OWNER_ANY_STATE` is for the surfaces that render
    inactive meetings themselves (the past-meetings screens, reactivation, the delete flows): the
    meeting's state is never an obstacle there, and a meeting that cannot be resolved is answered
    exactly like one the caller does not own.
    """

    OWNER = auto()
    OWNER_OR_JOINED = auto()
    OWNER_ANY_STATE = auto()


async def notify_meeting_not_owned(
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
):
    """Warn, count the rejection and send the user back to the main menu."""
    message = (
        f"User tried {action!r} with a meeting that does not belong to them. "
        f"Meeting id: {meeting_id}, user id: {user.db_id}"
    )
    log.warning(message)
    context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 1, unit=MetricUnit.COUNT)
    await context.api.edit_message(update=update, view=factory.main_menu_view(render_context(user, update, context)))


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
            render_context(user, update, context),
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


async def meeting(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    context: TMitupContext,
    *,
    access: MeetingAccess = MeetingAccess.OWNER,
    lock: bool = False,
    custom_keyboard: Keyboard | None = None,
) -> Meetup | None:
    """Return the meeting the user may act on through the bot chat, or None once the caller has
    been told why they cannot — on which handlers must bail immediately.

    The returned instance is always the guard's own meeting-rooted load, so its participant leaves
    (owner, joined links' user/invited_by) are hydrated and any renderer can traverse them under the
    async engine. `access` selects who gets through and which screen the rest are answered with.

    Participant- or capacity-mutating callers must pass `lock=True` so the meetup row (the
    per-meeting mutex) is locked before any capacity/waiting-list read, and so the ownership check
    itself runs on the locked row. Every decision the guard makes is meeting-rooted, so it never
    touches `user.meetups`/`user.joined_links`. Parameter mechanics such as `custom_keyboard` are
    documented in the guards skill.
    """
    found_meeting = await Meetup.by_id(session, meeting_id, for_update=lock)

    if access is MeetingAccess.OWNER_ANY_STATE:
        if found_meeting is None or not found_meeting.is_owned_by(user):
            await notify_meeting_not_owned(user, meeting_id, action, update, context)
            return None
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 0, unit=MetricUnit.COUNT)
        return found_meeting

    if found_meeting is None:
        await notify_meeting_removed(user, meeting_id, action, update, context, custom_keyboard)
        return None

    if not found_meeting.active:
        if found_meeting.is_owned_by(user):
            await show_reactivation_prompt(user, meeting_id, update, context, custom_keyboard)
        else:
            await notify_meeting_not_owned(user, meeting_id, action, update, context)
        return None

    if found_meeting.is_owned_by(user):
        context.emit_metric(MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), 0, unit=MetricUnit.COUNT)
        return found_meeting

    # The not-owned counter tracks ownership decisions, so a participant reaching a meeting they do
    # not own leaves the series untouched.
    if access is MeetingAccess.OWNER_OR_JOINED and found_meeting.has_participant(user.db_id):
        return found_meeting

    await notify_meeting_not_owned(user, meeting_id, action, update, context)
    return None


async def meeting_interaction_allowed(
    session: AsyncSession,
    user: User,
    meeting: Meetup,
    update: Update,
    context: TMitupContext,
) -> bool:
    """Return whether `user` may act on `meeting` through the message this update came from.

    Callback data is supplied by the client: a modified Telegram app can send any `data` bytes for
    any message the bot posted, so a meeting id read out of it proves nothing on its own. What the
    client cannot choose is where the tap happened — Telegram fills in the message coordinates
    (`chat_id`/`message_id`, `inline_message_id`, `chat_instance`) itself, which is what the
    message-bound arms below stand on.

    The caller is allowed when the meeting is public, when they own it, when they already have a
    membership (including the waiting list), when they invited somebody into it, when the tapped
    message is a tracked message of this meeting, when the meeting already has a tracked message in
    this chat, or when the tapped shared card is claimed by this meeting. Every one of those reads
    is rooted at the meeting, so `user` needs no loaded collections here.

    The claim on a shared card is recorded when the card is sent, by the `chosen_inline_result`
    handler — so a card that no meeting claims authorizes nothing: an attacker can share a card of
    their own at will, and honouring an unclaimed inline message would let them borrow it to act on
    any meeting id they can guess.

    Rejections are answered with the deleted-meeting copy, so a denial reveals nothing about the
    meeting's state — only that the id resolves, which sequential ids give away anyway.
    """
    meeting_id = meeting.db_id
    if (
        meeting.public
        or meeting.is_owned_by(user)
        or meeting.has_participant(user.db_id)
        or meeting.has_invited_participant(user.db_id)
    ):
        return True

    if meeting.message_from_update(update) is not None:
        return True

    query = update.callback_query

    chat_instance = query.chat_instance if query else None
    if chat_instance is not None and any(message.chat_instance == chat_instance for message in meeting.messages):
        return True

    inline_message_id = query.inline_message_id if query else None
    if inline_message_id is not None:
        claimed_by = await MessageModel.meetup_id_for_inline_message(session, inline_message_id)
        if claimed_by == meeting_id:
            return True

    await context.api.answer_callback_query(
        update=update,
        text=MeetingDisplayMessages.DELETED_BANNER.get(lang=user.lang),
        show_alert=True,
    )
    context.emit_metric(MetricKey.UNAUTHORIZED_MEETING_CALLBACK, include_handler_properties=False)
    return False


async def user_registered(
    update: Update,
    session: AsyncSession,
    context: TMitupContext,
    alert_message: MessageBase,
) -> User | None:
    """
    Context manager that yields the current user if they are subscribed to the bot.
    If the user is not subscribed, the callback query is answered with an allert showing the `alert_message`.

    The user comes back with no collections loaded, like `current_user`'s default.
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
