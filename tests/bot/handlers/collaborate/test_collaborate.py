from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from telegram import Update

from mitup_bot import patreon, supporter
from mitup_bot.config import BotConfig, LimitsConfig, PatreonConfig
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.handlers.collaborate.enums import CollaborateHandlerId
from mitup_bot.models import User
from mitup_bot.patreon import PatreonRuntime, oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import Link, render
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
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


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_deletes_subscription_and_revokes_premium(
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

    context, _ = await call_handler(CollaborateHandlerId.UNLINK, handler_context=handler_context)

    mock_session.assert_deleted(subscription)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    # The refreshed view confirms the unlink above the not-linked state.
    view = context.api.call_args("edit_message").kwargs["view"]
    assert view.description.text.startswith(CollaborateMessages.UNLINKED.get(lang=user_with_settings.lang).text)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_without_subscription_is_noop(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.UNLINK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    context.api.assert_method_just_called("edit_message", times=1)
