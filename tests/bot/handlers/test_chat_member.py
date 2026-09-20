import datetime as dt
from collections.abc import Callable, MutableMapping, Sequence
from typing import Any
from unittest import mock

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs
from telegram import (
    Chat,
    ChatMember,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    Update,
)
from telegram import (
    User as TgUser,
)
from telegram.error import Forbidden
from telegram.ext import ChatMemberHandler

from mitup_bot.config import BotConfig
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.exceptions import HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.chat_member import ChatMemberHandlerId, is_block_transition
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.views import factory
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    call_handler,
    create_member,
    create_user,
)
from tests.helpers.api import MockApi
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.monitoring import MetricAssertions

# --- Update builders ---

DATE = dt.datetime(2023, 1, 1, 12, 0, tzinfo=dt.UTC)
UNTIL = dt.datetime(2024, 1, 1, 12, 0, tzinfo=dt.UTC)

# The chat somebody added the bot to, and the two chats the configuration lets it stay in.
GROUP_CHAT_ID = -1009988776655
HOSTS_GROUP_CHAT_ID = -1001234567890
ALLOWED_GROUP_CHAT_ID = -1005555555555

ChatMemberFactory = Callable[[TgUser], ChatMember]


def make_tg_user() -> TgUser:
    return TgUser(**DEFAULT_TG_USER_PARAMS)


def make_admin_member(user: TgUser) -> ChatMemberAdministrator:
    """The bot as a plain administrator: every privilege off, which none of these tests read."""
    return ChatMemberAdministrator(
        user=user,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=False,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )


def make_restricted_member(user: TgUser, *, is_member: bool) -> ChatMemberRestricted:
    """The bot under restrictions. `is_member` is what tells a restricted arrival from a restricted
    user who has already left but keeps its restrictions on record."""
    return ChatMemberRestricted(
        user=user,
        is_member=is_member,
        until_date=UNTIL,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_send_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_manage_topics=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_react_to_messages=False,
        can_edit_tag=False,
    )


def make_chat_member_update(
    old: ChatMember,
    new: ChatMember,
    chat_type: str = Chat.PRIVATE,
    chat_id: int = DEFAULT_CHAT_ID,
) -> Update:
    tg_user = make_tg_user()
    return Update(
        update_id=1,
        my_chat_member=ChatMemberUpdated(
            chat=Chat(id=chat_id, type=chat_type),
            from_user=tg_user,
            date=DATE,
            old_chat_member=old,
            new_chat_member=new,
        ),
    )


def make_added_update(chat_type: str = Chat.SUPERGROUP, chat_id: int = GROUP_CHAT_ID) -> Update:
    """The bot being added to a chat: LEFT to MEMBER, which is what Telegram sends on an invite."""
    tg_user = make_tg_user()
    return make_chat_member_update(
        old=ChatMemberLeft(user=tg_user),
        new=ChatMemberMember(user=tg_user),
        chat_type=chat_type,
        chat_id=chat_id,
    )


def stash_membership_config(app: StubMitupApp, allowed_group_chat_ids: list[int]):
    """Replace the app's stashed BotConfig with one naming the hosts group and *allowed_group_chat_ids*."""
    app.bot_data[BOT_CONFIG_KEY] = BotConfig(
        token=SecretStr("test-token"),
        hosts_group_chat_id=HOSTS_GROUP_CHAT_ID,
        allowed_group_chat_ids=allowed_group_chat_ids,
    )


def make_block_update(chat_type: str = Chat.PRIVATE) -> Update:
    tg_user = make_tg_user()
    return make_chat_member_update(
        old=ChatMemberMember(user=tg_user),
        new=ChatMemberBanned(user=tg_user, until_date=UNTIL),
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
        new=ChatMemberMember(user=tg_user, until_date=UNTIL),
    )
    assert is_block_transition(update) is False


@pytest.mark.parametrize(
    ("old_factory", "new_factory"),
    [
        # unblock: BANNED -> MEMBER
        (lambda u: ChatMemberBanned(user=u, until_date=UNTIL), lambda u: ChatMemberMember(user=u)),
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
    """A known MEMBER user blocking the bot in a private chat flips status to LEFT and records both facts."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.MEMBER)
    mock_session.add_user(user)

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_block_update(), app, metrics_client),
        )

    assert user.status is UserStatus.LEFT
    blocked = next(entry for entry in logs if entry["event"] == "Bot blocked by user")
    assert blocked["tg_user_id"] == DEFAULT_CHAT_ID
    assert (blocked["old_bot_status"], blocked["new_bot_status"]) == ("member", "kicked")
    assert blocked["user_found"] is True
    assert blocked["demoted"] is True
    # The demotion itself is recorded once, on the event every other departure path shares.
    changed = next(entry for entry in logs if entry["event"] == "User status changed")
    assert changed["reason"] == "blocked_bot"
    assert changed["status"] == "left"


@pytest.mark.parametrize(
    ("old_factory", "new_factory"),
    [
        (lambda u: ChatMemberBanned(user=u, until_date=UNTIL), lambda u: ChatMemberMember(user=u)),
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
    """Unblock/rejoin transitions back to MEMBER are intentionally not handled: no status change, no line."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.LEFT)
    mock_session.add_user(user)
    update = make_chat_member_update(old=old_factory(make_tg_user()), new=new_factory(make_tg_user()))

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(update, app, metrics_client),
        )

    assert user.status is UserStatus.LEFT
    assert not [entry for entry in logs if entry["event"] == "Bot blocked by user"]


@pytest.mark.parametrize("chat_type", [Chat.GROUP, Chat.SUPERGROUP])
async def test_block_in_non_private_chat_is_noop(
    chat_type: str,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A MEMBER -> BANNED transition in a group/supergroup must not touch the user nor record a block."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=UserStatus.MEMBER)
    mock_session.add_user(user)

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_block_update(chat_type=chat_type), app, metrics_client),
        )

    assert user.status is UserStatus.MEMBER
    assert not [entry for entry in logs if entry["event"] == "Bot blocked by user"]


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
        new=ChatMemberMember(user=make_tg_user(), until_date=UNTIL),
    )

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(update, app, metrics_client),
        )

    assert user.status is UserStatus.MEMBER
    assert not [entry for entry in logs if entry["event"] == "Bot blocked by user"]


async def test_unknown_user_block_is_noop(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """A block by a tg_user_id absent from the DB still records the block, with nothing to demote."""
    # No user added to the session; lookup returns None.
    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_block_update(), app, metrics_client),
        )
    await context.flush_metrics()

    blocked = next(entry for entry in logs if entry["event"] == "Bot blocked by user")
    assert blocked["user_found"] is False
    assert blocked["demoted"] is False
    assert not [entry for entry in logs if entry["event"] == "User status changed"]
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


@pytest.mark.parametrize(
    "status",
    [UserStatus.LEFT, UserStatus.JOINED_ONLY],
    ids=["already_left", "joined_only"],
)
async def test_already_inactive_user_is_still_recorded_as_a_block(
    status: UserStatus,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A matched non-MEMBER user yields mark_inactive() == False, so nothing moves — but the block
    still happened, and `demoted=False` is what tells the two apart."""
    user = create_user(id=1, tg_user_id=DEFAULT_CHAT_ID, status=status)
    mock_session.add_user(user)

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_block_update(), app, metrics_client),
        )

    assert user.status is status
    blocked = next(entry for entry in logs if entry["event"] == "Bot blocked by user")
    assert blocked["user_found"] is True
    assert blocked["demoted"] is False
    assert not [entry for entry in logs if entry["event"] == "User status changed"]


# --- added to a chat ---

MEMBERSHIP_EVENT = "Bot group membership decided"


def membership_line(logs: Sequence[MutableMapping[str, Any]]) -> MutableMapping[str, Any]:
    return next(entry for entry in logs if entry["event"] == MEMBERSHIP_EVENT)


@pytest.mark.parametrize("chat_type", [Chat.GROUP, Chat.SUPERGROUP])
async def test_added_to_a_group_says_goodbye_and_leaves(
    chat_type: str,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A group nothing allows gets the farewell message, and the bot is gone right after it."""
    stash_membership_config(app, allowed_group_chat_ids=[])

    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(chat_type=chat_type), app, metrics_client),
        )

    context.api.assert_send_message_to_chat_called(GROUP_CHAT_ID, factory.group_farewell_view("en"))
    context.api.assert_method_just_called("leave_chat", times=1)
    line = membership_line(logs)
    assert line["log_level"] == "info"
    assert (line["reason"], line["outcome"]) == ("not_allowed", "left")
    assert line["chat_type"] == chat_type


@pytest.mark.parametrize(
    ("adder_lang", "expected_lang"),
    [
        pytest.param("es_ES", "es_ES", id="registered"),
        pytest.param(None, "en", id="unregistered"),
    ],
)
async def test_farewell_is_rendered_in_the_adder_language(
    adder_lang: str | None,
    expected_lang: str,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """The farewell speaks to whoever added the bot, and nobody needs an account to have done that.

    The view is spied on rather than compared: the catalogs carry no translation for this message
    yet, so two languages render the same bytes and a view-equality assertion would pass either way.
    """
    stash_membership_config(app, allowed_group_chat_ids=[])
    if adder_lang is not None:
        mock_session.add_user(create_member(id=1, tg_user_id=DEFAULT_TG_USER_PARAMS["id"], language=adder_lang))

    with mock.patch.object(factory, "group_farewell_view", wraps=factory.group_farewell_view) as build_view:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(), app, metrics_client),
        )

    build_view.assert_called_once_with(expected_lang)


@pytest.mark.parametrize(
    ("chat_id", "reason"),
    [
        pytest.param(HOSTS_GROUP_CHAT_ID, "hosts_group", id="hosts_group"),
        pytest.param(ALLOWED_GROUP_CHAT_ID, "allowlisted", id="allowlisted"),
    ],
)
async def test_added_to_an_allowed_group_stays(
    chat_id: int,
    reason: str,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """The hosts group and any configured allowlist entry keep the bot, silently and without a message."""
    stash_membership_config(app, allowed_group_chat_ids=[ALLOWED_GROUP_CHAT_ID])

    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(chat_id=chat_id), app, metrics_client),
        )

    context.api.assert_method_just_called("send_message_to_chat", times=0)
    context.api.assert_method_just_called("leave_chat", times=0)
    line = membership_line(logs)
    assert (line["reason"], line["outcome"]) == (reason, "stayed")


async def test_added_to_a_channel_leaves_without_posting(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A message in a channel is published to every subscriber, so the bot leaves without one."""
    stash_membership_config(app, allowed_group_chat_ids=[])

    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(chat_type=Chat.CHANNEL), app, metrics_client),
        )

    context.api.assert_method_just_called("send_message_to_chat", times=0)
    context.api.assert_method_just_called("leave_chat", times=1)
    line = membership_line(logs)
    assert (line["chat_type"], line["outcome"]) == (Chat.CHANNEL, "left")


async def test_farewell_refused_by_the_group_still_leaves(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A group that bars the bot from posting refuses the send; leaving never waits on it."""
    stash_membership_config(app, allowed_group_chat_ids=[])
    api = MockApi()
    api.register_on_method("send_message_to_chat", side_effect=Forbidden("Bot is not a member of the chat"))

    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(), app, metrics_client),
            api=api,
        )

    context.api.assert_method_just_called("leave_chat", times=1)
    refusal = next(entry for entry in logs if entry["event"] == "Group farewell message not delivered")
    assert refusal["log_level"] == "warning"
    assert refusal["exc_info"] is True
    assert membership_line(logs)["outcome"] == "left"


async def test_leave_refused_by_telegram_is_reported_as_a_failure(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A leave Telegram declines must not be recorded as a departure that happened."""
    stash_membership_config(app, allowed_group_chat_ids=[])
    api = MockApi()
    api.register_on_method("leave_chat", return_value=False)

    with capture_logs() as logs:
        await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(make_added_update(), app, metrics_client),
            api=api,
        )

    line = membership_line(logs)
    assert line["log_level"] == "warning"
    assert (line["reason"], line["outcome"]) == ("not_allowed", "leave_failed")


async def test_added_as_a_restricted_member_still_leaves(
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """A group that admits the bot under restrictions has still added it, so the bot still goes."""
    stash_membership_config(app, allowed_group_chat_ids=[])
    tg_user = make_tg_user()
    update = make_chat_member_update(
        old=ChatMemberLeft(user=tg_user),
        new=make_restricted_member(tg_user, is_member=True),
        chat_type=Chat.SUPERGROUP,
        chat_id=GROUP_CHAT_ID,
    )

    context, _ = await call_handler(
        ChatMemberHandlerId.MY_CHAT_MEMBER,
        handler_context=make_handler_context(update, app, metrics_client),
    )

    context.api.assert_method_just_called("send_message_to_chat", times=1)
    context.api.assert_method_just_called("leave_chat", times=1)


@pytest.mark.parametrize(
    ("old_factory", "new_factory"),
    [
        (lambda u: ChatMemberMember(user=u), make_admin_member),
        (lambda u: ChatMemberMember(user=u), lambda u: ChatMemberLeft(user=u)),
        # A restricted non-member is out of the chat, so nothing has arrived to be shown the door.
        (lambda u: ChatMemberLeft(user=u), lambda u: make_restricted_member(u, is_member=False)),
    ],
    ids=["promoted_to_administrator", "removed_from_the_group", "restricted_without_membership"],
)
async def test_group_transition_that_is_not_a_join_is_a_noop(
    old_factory: ChatMemberFactory,
    new_factory: ChatMemberFactory,
    mock_session: MockDbSession,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    """Only arriving in a chat decides membership: a promotion or a removal moves nothing."""
    stash_membership_config(app, allowed_group_chat_ids=[])
    tg_user = make_tg_user()
    update = make_chat_member_update(
        old=old_factory(tg_user),
        new=new_factory(tg_user),
        chat_type=Chat.SUPERGROUP,
        chat_id=GROUP_CHAT_ID,
    )

    with capture_logs() as logs:
        context, _ = await call_handler(
            ChatMemberHandlerId.MY_CHAT_MEMBER,
            handler_context=make_handler_context(update, app, metrics_client),
        )

    context.api.assert_method_just_called("send_message_to_chat", times=0)
    context.api.assert_method_just_called("leave_chat", times=0)
    assert not [entry for entry in logs if entry["event"] == MEMBERSHIP_EVENT]


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
