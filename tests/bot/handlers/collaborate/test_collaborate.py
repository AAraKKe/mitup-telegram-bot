from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from telegram import Update

from mitup_bot import patreon, supporter
from mitup_bot.config import BotConfig, LimitsConfig, PatreonConfig
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.handlers.collaborate.enums import CollaborateHandlerId
from mitup_bot.hosts_group import HostsGroupState
from mitup_bot.models import User
from mitup_bot.patreon import PatreonRuntime, oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import Link, render
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    hosts_group_removed_view,
)
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    HandlerContext,
    UpdateRequest,
    call_handler,
    create_patreon_config,
    create_supporter_subscription,
)
from tests.helpers.api import MockApi
from tests.helpers.stub_db import MockDbSession

PATRON_ACTIVE_MEETINGS = 12
PATRON_SCHEDULING_DAYS = 200
HOSTS_GROUP_CHAT_ID = -1001234567890
HOSTS_GROUP_INVITE_URL = "https://t.me/+hostsonlyinvite"


def stash_hosts_group_config(handler_context: HandlerContext):
    """Replace the app's stashed BotConfig with one that enables the Hosts-Only Group feature."""
    handler_context.app.bot_data[BOT_CONFIG_KEY] = BotConfig(
        token=SecretStr("test-token"),
        hosts_group_chat_id=HOSTS_GROUP_CHAT_ID,
        hosts_group_invite_url=HOSTS_GROUP_INVITE_URL,
    )


def has_hosts_group_button(view: MitupView) -> bool:
    """Whether the rendered Collaborate view exposes the Hosts-Only Group access button."""
    return any(button.url == HOSTS_GROUP_INVITE_URL for row in view.keyboard for button in row)


@pytest.fixture
def hosts_group_state() -> Iterator[None]:
    """Enable the hosts-only group at the process-wide holder the withdrawal path reads."""
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    HostsGroupState.invite_url = HOSTS_GROUP_INVITE_URL
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


@pytest.fixture
def hosts_group_disabled() -> Iterator[None]:
    """Force the hosts-only group off at the process-wide holder, whatever earlier tests left."""
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = None
    HostsGroupState.invite_url = None
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


@pytest.fixture
def patreon_config() -> Iterator[PatreonConfig]:
    """Configure Patreon for the duration of a test and restore the prior state afterwards."""
    saved = PatreonRuntime.config
    config = create_patreon_config(campaign_id="12345")
    patreon.configure(config)
    try:
        yield config
    finally:
        PatreonRuntime.config = saved


@pytest.fixture(autouse=True)
def reset_patreon() -> Iterator[None]:
    """Guarantee the process-wide holder is unconfigured unless a test opts in."""
    saved = PatreonRuntime.config
    PatreonRuntime.config = None
    try:
        yield
    finally:
        PatreonRuntime.config = saved


@pytest.fixture(autouse=True)
def patron_caps(monkeypatch: pytest.MonkeyPatch):
    """Pin the Patron-tier caps so the Collaborate copy renders deterministic numbers."""
    config = LimitsConfig(
        patron_active_meetings=PATRON_ACTIVE_MEETINGS,
        patron_scheduling_horizon_days=PATRON_SCHEDULING_DAYS,
    )
    monkeypatch.setattr(supporter.PolicyState, "config", config)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
async def test_collaborate_not_linked_offers_oauth_link(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    collaborate_page = render(
        t"{Link(CollaborateMessages.COLLABORATE_PAGE_LABEL.get_text(lang=user_with_settings.lang), 'https://mitup.social/collaborate/donation/')}"
    )
    limits_page = render(
        t"{Link(CollaborateMessages.LIMITS_PAGE_LABEL.get_text(lang=user_with_settings.lang), 'https://mitup.social/user-guide/limits/')}"
    )
    assert view.description == CollaborateMessages.NOT_LINKED.get(
        lang=user_with_settings.lang,
        collaborate_page=collaborate_page,
        limits_page=limits_page,
    )
    assert "${" not in view.description.text
    link_button = view.keyboard[0][0]
    assert link_button.url.startswith("https://www.patreon.com/oauth2/authorize")
    # The button anyone can be handed carries a state that identifies nobody: it validates, and
    # that is all it does.
    state = parse_qs(urlparse(link_button.url).query)["state"][0]
    oauth.validate_state(patreon_config, state)
    assert str(user_with_settings.tg_user_id) not in link_button.url


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
async def test_collaborate_linked_not_patron_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    user_with_settings.supporter_level = SupporterLevel.NONE
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        collaborate_linked_not_patron_view(
            user_with_settings.lang,
            oauth.campaign_pledge_url(patreon_config),
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
@pytest.mark.parametrize(
    "level,expected_message",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.HOST_2, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.HOST_3, CollaborateMessages.LINKED_PATRON_ORGANIZER),
    ],
)
async def test_collaborate_linked_patron_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    level: SupporterLevel,
    expected_message: CollaborateMessages,
):
    user_with_settings.supporter_level = level
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        collaborate_linked_patron_view(user_with_settings.lang, level, PATRON_ACTIVE_MEETINGS, PATRON_SCHEDULING_DAYS),
    )
    # The rendered screen uses the message tied to the user's own tier, not a generic one.
    view = context.api.call_args("edit_message").kwargs["view"]
    assert view.description == expected_message.get(
        lang=user_with_settings.lang,
        active_meetings=PATRON_ACTIVE_MEETINGS,
        scheduling_days=PATRON_SCHEDULING_DAYS,
    )
    assert "${" not in view.description.text


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
@pytest.mark.parametrize(
    "in_group,expected_label",
    [
        (True, ButtonMessages.HOSTS_GROUP_OPEN),
        (False, ButtonMessages.HOSTS_GROUP_JOIN),
    ],
)
async def test_collaborate_patron_shows_hosts_group_button(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    in_group: bool,
    expected_label: ButtonMessages,
):
    stash_hosts_group_config(handler_context)
    api = MockApi()
    member_check = api.register_on_method("is_chat_member", return_value=in_group)
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context, api=api)

    member_check.assert_awaited_once_with(chat_id=HOSTS_GROUP_CHAT_ID, tg_user_id=user_with_settings.tg_user_id)
    view = context.api.call_args("edit_message").kwargs["view"]
    group_button = view.keyboard[0][0]
    assert group_button.text == expected_label.get_text(lang=user_with_settings.lang)
    assert group_button.url == HOSTS_GROUP_INVITE_URL


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
async def test_collaborate_patron_omits_hosts_group_button_when_unconfigured(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    # The default stashed BotConfig leaves both hosts-group values None, so the feature is disabled:
    # the group membership lookup must be skipped and only the Unlink button rendered.
    api = MockApi()
    member_check = api.register_on_method("is_chat_member", return_value=True)
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context, api=api)

    member_check.assert_not_awaited()
    context.api.assert_edit_message_called(
        update,
        collaborate_linked_patron_view(
            user_with_settings.lang, SupporterLevel.HOST_2, PATRON_ACTIVE_MEETINGS, PATRON_SCHEDULING_DAYS
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
@pytest.mark.parametrize("level", [SupporterLevel.HOST_1, SupporterLevel.HOST_2, SupporterLevel.HOST_3])
async def test_hosts_group_button_present_for_any_linked_host(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    level: SupporterLevel,
):
    """A linked, active host sees the group button regardless of which host tier they hold."""
    stash_hosts_group_config(handler_context)
    api = MockApi()
    api.register_on_method("is_chat_member", return_value=False)
    user_with_settings.supporter_level = level
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context, api=api)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert has_hosts_group_button(view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
async def test_hosts_group_button_absent_when_not_linked(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    """A user who never linked Patreon is in the not-linked state, so the group button is absent
    and no membership lookup runs even with the feature configured."""
    stash_hosts_group_config(handler_context)
    api = MockApi()
    member_check = api.register_on_method("is_chat_member", return_value=True)
    user_with_settings.supporter_level = SupporterLevel.NONE
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context, api=api)

    member_check.assert_not_awaited()
    view = context.api.call_args("edit_message").kwargs["view"]
    assert not has_hosts_group_button(view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
async def test_hosts_group_button_absent_when_linked_not_patron(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    """A linked user who is not an active patron sees no group button, and no membership lookup runs."""
    stash_hosts_group_config(handler_context)
    api = MockApi()
    member_check = api.register_on_method("is_chat_member", return_value=True)
    user_with_settings.supporter_level = SupporterLevel.NONE
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context, api=api)

    member_check.assert_not_awaited()
    view = context.api.call_args("edit_message").kwargs["view"]
    assert not has_hosts_group_button(view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK)], indirect=True)
async def test_confirming_unlink_deletes_subscription_and_revokes_premium(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_CONFIRM, handler_context=handler_context)

    mock_session.assert_deleted(subscription)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    # The refreshed view confirms the unlink above the not-linked state.
    view = context.api.call_args("edit_message").kwargs["view"]
    assert view.description.text.startswith(CollaborateMessages.UNLINKED.get(lang=user_with_settings.lang).text)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK)], indirect=True)
async def test_confirming_unlink_without_subscription_is_noop(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_CONFIRM, handler_context=handler_context)

    mock_session.assert_not_deleted()
    context.api.assert_method_just_called("edit_message", times=1)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK)], indirect=True)
async def test_confirming_unlink_removes_the_former_host_from_the_hosts_group(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    hosts_group_state: None,
):
    # Deleting the row removes this user from every daily sweep, so the unlink itself settles group
    # membership: the member is ejected (ban then unban — a kick, never a permanent ban) and told
    # why, exactly as a lapse would have told them.
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")
    api = MockApi()
    api.register_on_method("is_chat_member", return_value=True)

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_CONFIRM, handler_context=handler_context, api=api)

    mock_session.assert_deleted(subscription)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    api.assert_method_just_called("ban_chat_member", times=1)
    api.assert_method_just_called("unban_chat_member", times=1)
    assert api.call_args("unban_chat_member").kwargs == {
        "chat_id": HOSTS_GROUP_CHAT_ID,
        "tg_user_id": user_with_settings.tg_user_id,
        "only_if_banned": True,
    }
    removal_view = api.call_args("send_message_to_user").kwargs["view"]
    assert removal_view == hosts_group_removed_view(user_with_settings.lang)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK)], indirect=True)
async def test_confirming_unlink_clears_the_ban_a_past_revoke_left(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    hosts_group_state: None,
):
    # A lapsed host was banned by the daily job and now unlinks. With the row gone nothing can ever
    # nominate them for readmission, so the unlink lifts the ban; the join-request gate still
    # declines them for as long as they are not a supporter. They were already out of the group, so
    # there is nothing to announce.
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")
    api = MockApi()

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_CONFIRM, handler_context=handler_context, api=api)

    mock_session.assert_deleted(subscription)
    api.assert_method_just_called("ban_chat_member", times=0)
    api.assert_method_just_called("unban_chat_member", times=1)
    api.mock_method("send_message_to_user").assert_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK)], indirect=True)
async def test_confirming_unlink_leaves_the_group_alone_when_unconfigured(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
    hosts_group_disabled: None,
):
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")
    api = MockApi()

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_CONFIRM, handler_context=handler_context, api=api)

    mock_session.assert_deleted(subscription)
    api.assert_method_just_called("ban_chat_member", times=0)
    api.assert_method_just_called("unban_chat_member", times=0)
    api.mock_method("is_chat_admin").assert_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_button_opens_a_prompt_and_deletes_nothing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    # Unlinking a Host switches their perks off and ends their group access, so one tap must not be
    # enough. The Host variant names the tier being given up; nothing is written by the prompt.
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    assert user_with_settings.supporter_level is SupporterLevel.HOST_2
    view = context.api.call_args("edit_message").kwargs["view"]
    assert view.description == CollaborateMessages.UNLINK_CONFIRM_HOST.get(
        lang=user_with_settings.lang,
        current_tier=CollaborateMessages.TIER_NAME_HOST_2.get_text(lang=user_with_settings.lang),
    )
    assert "Gamemaster" in view.description.text
    assert "${" not in view.description.text
    confirm, decline = view.keyboard[0]
    assert confirm.callback_data == cb.CONFIRM_PATREON_UNLINK
    assert decline.callback_data == cb.DECLINE_PATREON_UNLINK
    assert confirm.text == ButtonMessages.CONFIRM_PATREON_UNLINK.get_text(lang=user_with_settings.lang)
    assert decline.text == ButtonMessages.DECLINE_PATREON_UNLINK.get_text(lang=user_with_settings.lang)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_prompt_reads_plain_for_a_non_supporter(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    # A linked non-supporter has no perks to lose, so warning them about a tier or the group would
    # be false; they get the variant that only describes the disconnection.
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    view = context.api.call_args("edit_message").kwargs["view"]
    assert view.description == CollaborateMessages.UNLINK_CONFIRM.get(lang=user_with_settings.lang)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_button_with_nothing_linked_skips_the_prompt(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    # A stale Unlink button on an already-unlinked account: a prompt about a connection that does
    # not exist would be a lie, so the handler renders the current (not-linked) screen instead.
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    view = context.api.call_args("edit_message").kwargs["view"]
    collaborate_page = render(
        t"{Link(CollaborateMessages.COLLABORATE_PAGE_LABEL.get_text(lang=user_with_settings.lang), 'https://mitup.social/collaborate/donation/')}"
    )
    limits_page = render(
        t"{Link(CollaborateMessages.LIMITS_PAGE_LABEL.get_text(lang=user_with_settings.lang), 'https://mitup.social/user-guide/limits/')}"
    )
    assert view.description == CollaborateMessages.NOT_LINKED.get(
        lang=user_with_settings.lang,
        collaborate_page=collaborate_page,
        limits_page=limits_page,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_PATREON_UNLINK)], indirect=True)
async def test_declining_unlink_returns_to_collaborate_unchanged(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="patreon-1")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK_DECLINE, handler_context=handler_context)

    mock_session.assert_not_deleted()
    assert user_with_settings.supporter_level is SupporterLevel.HOST_2
    context.api.assert_edit_message_called(
        update,
        collaborate_linked_patron_view(
            user_with_settings.lang, SupporterLevel.HOST_2, PATRON_ACTIVE_MEETINGS, PATRON_SCHEDULING_DAYS
        ),
    )
