from enum import Enum, auto
from typing import Literal, cast, overload

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
    MeetingGoneError,
    MeetingInactiveOwnerError,
    MeetingNotOwnedError,
    SharedMeetingDeniedError,
    SharedMeetingFinishedError,
    SharedMeetingGoneError,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.handler_id import HandlerId
from mitup_bot.keyboards import Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import MessageBase
from mitup_bot.views import RenderContext

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
    """Which callers `meeting` lets through, and which rejection stops the rest.

    `OWNER`, `OWNER_OR_JOINED` and `OWNER_OR_PUBLIC` are the live-meeting profiles: a meeting that no
    longer exists raises `MeetingGoneError`, and an inactive one raises `MeetingInactiveOwnerError`
    for its owner and `MeetingNotOwnedError` for everybody else. `OWNER_ANY_STATE` is for the surfaces
    that render inactive meetings themselves (the past-meetings screens, reactivation, the delete
    flows): the meeting's state is never an obstacle there, and a meeting that cannot be resolved is
    rejected exactly like one the caller does not own.

    `OWNER_OR_PUBLIC` is the only profile whose caller may have no account at all, since being public
    is the whole permission it grants.
    """

    OWNER = auto()
    OWNER_OR_JOINED = auto()
    OWNER_OR_PUBLIC = auto()
    OWNER_ANY_STATE = auto()


@overload
async def meeting(
    session: AsyncSession,
    user: User | None,
    meeting_id: int,
    action: str,
    context: TMitupContext,
    *,
    access: Literal[MeetingAccess.OWNER_OR_PUBLIC],
    lock: bool = False,
    custom_keyboard: Keyboard | None = None,
    flow_context: MessageBase | None = None,
) -> Meetup: ...


@overload
async def meeting(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    context: TMitupContext,
    *,
    access: MeetingAccess = ...,
    lock: bool = False,
    custom_keyboard: Keyboard | None = None,
    flow_context: MessageBase | None = None,
) -> Meetup: ...


async def meeting(
    session: AsyncSession,
    user: User | None,
    meeting_id: int,
    action: str,
    context: TMitupContext,
    *,
    access: MeetingAccess = MeetingAccess.OWNER,
    lock: bool = False,
    custom_keyboard: Keyboard | None = None,
    flow_context: MessageBase | None = None,
) -> Meetup:
    """Return the meeting the user may act on through the bot chat, or raise the rejection that
    says why they cannot — on which the handler is over and the error handler answers the caller.

    The returned instance is always the guard's own meeting-rooted load, so its participant leaves
    (owner, joined links' user/invited_by) are hydrated and any renderer can traverse them under the
    async engine. `access` selects who gets through and which `MeetingAccessError` stops the rest;
    each of those carries the screen's language and back-navigation rows, so the guard renders
    nothing itself and needs no `update`.

    `user` is `None` only on `OWNER_OR_PUBLIC`, the surface a Telegram user with no account reaches:
    such a caller owns nothing, so every ownership test short-circuits and the only way past the
    guard is the meeting's own `public` flag. Their rejections carry the fallback language, since
    there is no account to read a preference from.

    Participant- or capacity-mutating callers must pass `lock=True` so the meetup row (the
    per-meeting mutex) is locked before any capacity/waiting-list read, and so the ownership check
    itself runs on the locked row. Every decision the guard makes is meeting-rooted, so it never
    touches `user.meetups`/`user.joined_links`. Parameter mechanics such as `custom_keyboard` and
    `flow_context` are documented in the guards skill.
    """
    found_meeting = await Meetup.by_id(session, meeting_id, for_update=lock)
    lang = user.lang if user else TranslationEngine.FALLBACK_LANG
    user_db_id = user.db_id if user else None

    if access is MeetingAccess.OWNER_ANY_STATE:
        if found_meeting is None or user is None or not found_meeting.is_owned_by(user):
            raise MeetingNotOwnedError(
                meeting_id=meeting_id,
                action=action,
                user_db_id=user_db_id,
                lang=lang,
                flow_context=flow_context,
            )
        context.emit_metric(MetricKey.MEETING_NOT_OWNED, 0, unit=MetricUnit.COUNT)
        return found_meeting

    if found_meeting is None:
        raise MeetingGoneError(
            meeting_id=meeting_id,
            action=action,
            user_db_id=user_db_id,
            lang=lang,
            keyboard=custom_keyboard,
            flow_context=flow_context,
        )

    if not found_meeting.active:
        if user is not None and found_meeting.is_owned_by(user):
            raise MeetingInactiveOwnerError(
                meeting_id=meeting_id,
                action=action,
                user_db_id=user.db_id,
                lang=user.lang,
                keyboard=custom_keyboard,
                flow_context=flow_context,
            )
        raise MeetingNotOwnedError(
            meeting_id=meeting_id, action=action, user_db_id=user_db_id, lang=lang, flow_context=flow_context
        )

    if user is not None and found_meeting.is_owned_by(user):
        context.emit_metric(MetricKey.MEETING_NOT_OWNED, 0, unit=MetricUnit.COUNT)
        return found_meeting

    # The not-owned counter tracks ownership decisions, so a caller reaching a meeting they do not
    # own through one of the wider profiles leaves the series untouched.
    if access is MeetingAccess.OWNER_OR_JOINED and user is not None and found_meeting.has_participant(user.db_id):
        return found_meeting

    if access is MeetingAccess.OWNER_OR_PUBLIC and found_meeting.public:
        return found_meeting

    raise MeetingNotOwnedError(
        meeting_id=meeting_id, action=action, user_db_id=user_db_id, lang=lang, flow_context=flow_context
    )


async def meeting_interaction_allowed(
    session: AsyncSession,
    user: User | None,
    meeting: Meetup,
    update: Update,
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

    `user` is `None` when the Telegram user tapping has no account. They own nothing, hold no
    membership and have invited nobody, so the three user-rooted arms short-circuit and the only
    ways past are the meeting's own `public` flag and the message-bound arms — which is exactly the
    "you are tapping a real card this meeting claims" test.

    The claim on a shared card is recorded when the card is sent, by the `chosen_inline_result`
    handler — so a card that no meeting claims authorizes nothing: an attacker can share a card of
    their own at will, and honouring an unclaimed inline message would let them borrow it to act on
    any meeting id they can guess.

    This is the predicate on its own; `shared_meeting` turns a `False` into the rejection the caller
    is answered with.
    """
    meeting_id = meeting.db_id
    if meeting.public:
        return True

    if user is not None and (
        meeting.is_owned_by(user) or meeting.has_participant(user.db_id) or meeting.has_invited_participant(user.db_id)
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

    return False


@overload
async def shared_meeting(
    session: AsyncSession,
    user: User | None,
    meeting_id: int,
    action: str,
    update: Update,
    *,
    allow_anonymous: Literal[True],
    lock: bool = False,
    require_active: bool = True,
) -> Meetup: ...


@overload
async def shared_meeting(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    update: Update,
    *,
    allow_anonymous: bool = ...,
    lock: bool = False,
    require_active: bool = True,
) -> Meetup: ...


async def shared_meeting(
    session: AsyncSession,
    user: User | None,
    meeting_id: int,
    action: str,
    update: Update,
    *,
    allow_anonymous: bool = False,
    lock: bool = False,
    require_active: bool = True,
) -> Meetup:
    """Return the meeting the caller tapped a card for, or raise the rejection that answers the card.

    The single guard for the any-user surfaces — join, leave, attach-to-chat and the invite entry
    point — where the meeting is reached from a card that may sit in any chat it was shared into,
    and the caller needs no relationship with the meeting to have tapped it. `action` and the
    `SharedMeetingError` payload work exactly as they do for `meeting`; the error handler owns every
    screen, so this guard renders nothing and takes no `context`.

    The three rejections, in the order they are decided:

    - the meeting does not resolve — `SharedMeetingGoneError`, the card is stale;
    - the tapped message gives the caller no claim on it — `SharedMeetingDeniedError`, decided before
      the meeting's state is read so a denial discloses nothing about it;
    - the meeting is no longer active — `SharedMeetingFinishedError`, unless `require_active` is
      False, which is for the surfaces that stay useful on a finished meeting.

    `allow_anonymous=True` is what lets `user` be `None`, and the signature enforces it: only that
    call shape type-checks a `User | None`, so a surface that means to serve a caller with no account
    says so here and every other one keeps its `User` guarantee. Holding the card is the whole
    prerequisite for tapping it, and the decision this guard takes for such a caller is the
    message-bound one. Their rejections carry the fallback language, since there is no account to
    read a preference from, and name an anonymous caller. The flag widens the type only — a `None`
    user takes the same path whatever it is set to.

    Pass `lock=True` from the participant- or capacity-mutating callers, as for `meeting`. The locked
    load resets the acting user's `meetups`/`joined_links` to unloaded; the guard itself needs
    neither, so a caller that reads them re-loads them after this returns.
    """
    found_meeting = await Meetup.by_id(session, meeting_id, for_update=lock)
    lang = user.lang if user else TranslationEngine.FALLBACK_LANG
    user_db_id = user.db_id if user else None

    if found_meeting is None:
        raise SharedMeetingGoneError(meeting_id=meeting_id, action=action, user_db_id=user_db_id, lang=lang)

    if not await meeting_interaction_allowed(session, user, found_meeting, update):
        raise SharedMeetingDeniedError(meeting_id=meeting_id, action=action, user_db_id=user_db_id, lang=lang)

    if require_active and not found_meeting.active:
        raise SharedMeetingFinishedError(
            meeting_id=meeting_id, action=action, user_db_id=user_db_id, lang=found_meeting.lang
        )

    return found_meeting


async def conversation_meeting(
    session: AsyncSession,
    user: User,
    meeting_id: int,
    action: str,
    *,
    lock: bool = False,
    flow_context: MessageBase | None = None,
) -> Meetup:
    """Return the active meeting an in-flight conversation is about, or raise `MeetingGoneError`.

    The id comes from the caller's own conversation state, so it already carries whatever
    authorization the entry point made and no access decision is re-taken here. What can still change
    while the user types is the meeting itself: this re-reads it and rejects a meeting that has since
    been deleted or deactivated, so no step writes to one that is no longer there.

    Pass `lock=True` from the step that commits the flow's write, as for `meeting`: the row lock is
    then held from this read through the capacity decision and the insert that follows it.
    `flow_context` travels to the rejection screen as for `meeting`, and is where a mid-flow step
    names the flow the caller is being taken out of.
    """
    found_meeting = await Meetup.by_id(session, meeting_id, include_inactive=False, for_update=lock)
    if found_meeting is None:
        raise MeetingGoneError(
            meeting_id=meeting_id, action=action, user_db_id=user.db_id, lang=user.lang, flow_context=flow_context
        )
    return found_meeting


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
