import datetime as dt
from collections.abc import Callable

import pytest
from telegram import (
    Chat,
    ChatMember,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Update,
)
from telegram import (
    User as TgUser,
)
from telegram.ext import ChatMemberHandler

from mitup_bot.exceptions import HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.chat_member import ChatMemberHandlerId, is_block_transition
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.helpers import HandlerContext, MockDbSession, StubMitupApp, call_handler, create_user
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.monitoring import MetricAssertions

# --- Update builders ---

_DATE = dt.datetime(2023, 1, 1, 12, 0, tzinfo=dt.UTC)
_UNTIL = dt.datetime(2024, 1, 1, 12, 0, tzinfo=dt.UTC)

ChatMemberFactory = Callable[[TgUser], ChatMember]


def make_tg_user() -> TgUser:
    return TgUser(**DEFAULT_TG_USER_PARAMS)


def make_chat_member_update(
    old: ChatMember,
    new: ChatMember,
    chat_type: str = Chat.PRIVATE,
) -> Update:
    tg_user = make_tg_user()
    return Update(
        update_id=1,
        my_chat_member=ChatMemberUpdated(
            chat=Chat(id=DEFAULT_CHAT_ID, type=chat_type),
            from_user=tg_user,
            date=_DATE,
            old_chat_member=old,
            new_chat_member=new,
        ),
    )


def make_block_update(chat_type: str = Chat.PRIVATE) -> Update:
    tg_user = make_tg_user()
    return make_chat_member_update(
        old=ChatMemberMember(user=tg_user),
        new=ChatMemberBanned(user=tg_user, until_date=_UNTIL),
        chat_type=chat_type,
    )


# --- is_block_transition (unit) ---


def test_is_block_transition_member_to_banned_private():
    assert is_block_transition(make_block_update()) is True


def test_is_block_transition_none_my_chat_member():
    # A plain update with no my_chat_member is never a block transition.
    assert is_block_transition(Update(update_id=1)) is False


@pytest.mark.parametrize(
    "chat_type",
    [Chat.GROUP, Chat.SUPERGROUP, Chat.CHANNEL],
)
def test_is_block_transition_non_private_chat(chat_type: str):
    assert is_block_transition(make_block_update(chat_type=chat_type)) is False


def test_is_block_transition_no_status_key():
    tg_user = make_tg_user()
    # Both members are MEMBER; only until_date changes, so difference() has no "status" key.
    update = make_chat_member_update(
        old=ChatMemberMember(user=tg_user),
        new=ChatMemberMember(user=tg_user, until_date=_UNTIL),
    )
    assert is_block_transition(update) is False


@pytest.mark.parametrize(
    ("old_factory", "new_factory"),
    [
        # unblock: BANNED -> MEMBER
        (lambda u: ChatMemberBanned(user=u, until_date=_UNTIL), lambda u: ChatMemberMember(user=u)),
        # rejoin: LEFT -> MEMBER
        (lambda u: ChatMemberLeft(user=u), lambda u: ChatMemberMember(user=u)),
        # member -> left (not a ban)
        (lambda u: ChatMemberMember(user=u), lambda u: ChatMemberLeft(user=u)),
    ],
    ids=["banned_to_member", "left_to_member", "member_to_left"],
)
def test_is_block_transition_irrelevant_status_change(old_factory: ChatMemberFactory, new_factory: ChatMemberFactory):
    tg_user = make_tg_user()
    update = make_chat_member_update(old=old_factory(tg_user), new=new_factory(tg_user))
    assert is_block_transition(update) is False


# --- handler behaviour ---


def make_handler_context(update: Update, app: StubMitupApp, metrics_client: MetricsClient) -> HandlerContext:
    return HandlerContext(update=update, app=app, metrics_client=metrics_client)


async def test_block_flips_member_to_left_and_emits_metric(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A known MEMBER user blocking the bot in a private chat flips status to LEFT and emits the metric once."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.MEMBER)
    mock_session.add_user(user)

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(make_block_update(), app, metrics_client),
    )
    await context.flush_metrics()

    assert user.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1, times=1)


@pytest.mark.parametrize(
    ("old_factory", "new_factory"),
    [
        (lambda u: ChatMemberBanned(user=u, until_date=_UNTIL), lambda u: ChatMemberMember(user=u)),
        (lambda u: ChatMemberLeft(user=u), lambda u: ChatMemberMember(user=u)),
    ],
    ids=["banned_to_member", "left_to_member"],
)
async def test_unblock_transition_is_noop(
    old_factory: ChatMemberFactory,
    new_factory: ChatMemberFactory,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """Unblock/rejoin transitions back to MEMBER are intentionally not handled: no status change, no metric."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.LEFT)
    mock_session.add_user(user)
    update = make_chat_member_update(old=old_factory(make_tg_user()), new=new_factory(make_tg_user()))

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(update, app, metrics_client),
    )
    await context.flush_metrics()

    assert user.status is UserStatus.LEFT
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


@pytest.mark.parametrize("chat_type", [Chat.GROUP, Chat.SUPERGROUP])
async def test_block_in_non_private_chat_is_noop(
    chat_type: str,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A MEMBER -> BANNED transition in a group/supergroup must not touch the user nor emit the metric."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.MEMBER)
    mock_session.add_user(user)

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(make_block_update(chat_type=chat_type), app, metrics_client),
    )
    await context.flush_metrics()

    assert user.status is UserStatus.MEMBER
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_irrelevant_status_change_is_noop(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A private update whose difference() has no 'status' key (only until_date changed) is a no-op."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.MEMBER)
    mock_session.add_user(user)
    update = make_chat_member_update(
        old=ChatMemberMember(user=make_tg_user()),
        new=ChatMemberMember(user=make_tg_user(), until_date=_UNTIL),
    )

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(update, app, metrics_client),
    )
    await context.flush_metrics()

    assert user.status is UserStatus.MEMBER
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_unknown_user_block_is_noop(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A block transition for a tg_user_id absent from the DB returns silently with no metric and no exception."""
    # No user added to the session; lookup returns None.
    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(make_block_update(), app, metrics_client),
    )
    await context.flush_metrics()

    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


@pytest.mark.parametrize(
    "status",
    [UserStatus.LEFT, UserStatus.JOINED_ONLY],
    ids=["already_left", "joined_only"],
)
async def test_already_inactive_user_does_not_emit_metric(
    status: UserStatus,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A matched non-MEMBER user yields mark_inactive() == False, so no metric fires despite the block transition."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=status)
    mock_session.add_user(user)

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(make_block_update(), app, metrics_client),
    )
    await context.flush_metrics()

    assert user.status is status
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


# --- registry wiring ---


def test_chat_member_handler_registered():
    wrapper = HandlersRegistry.handlers[ChatMemberHandlerId.MY_CHAT_MEMBER]

    assert isinstance(wrapper.handler, ChatMemberHandler)
    assert wrapper.handler.chat_member_types == ChatMemberHandler.MY_CHAT_MEMBER
    assert wrapper.bindable is True


def test_register_chat_member_twice_raises():
    class DuplicateChatMemberId(HandlerId):
        DUP = "dup_chat_member"

    @HandlersRegistry.register_chat_member(handler_id=DuplicateChatMemberId.DUP)
    async def first_handler(update: Update, context: object):
        return None

    try:
        with pytest.raises(HandlerRegisteredError):

            @HandlersRegistry.register_chat_member(handler_id=DuplicateChatMemberId.DUP)
            async def second_handler(update: Update, context: object):
                return None
    finally:
        # Always restore the global registry, even if the assertion above fails.
        HandlersRegistry.handlers.pop(DuplicateChatMemberId.DUP, None)
