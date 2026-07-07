from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from telegram import Update

from mitup_bot import patreon, supporter
from mitup_bot.config import LimitsConfig, PatreonConfig
from mitup_bot.handlers.collaborate.enums import CollaborateHandlerId
from mitup_bot.models import User
from mitup_bot.patreon import PatreonRuntime, oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_unavailable_view,
)
from tests.helpers import (
    HandlerContext,
    UpdateRequest,
    call_handler,
    create_patreon_config,
    create_supporter_subscription,
)
from tests.helpers.constants import DEFAULT_MESSAGE_ID
from tests.helpers.stub_db import MockDbSession

PATRON_ACTIVE_MEETINGS = 12
PATRON_SCHEDULING_DAYS = 200


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
async def test_collaborate_unavailable_when_patreon_unconfigured(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(CollaborateHandlerId.SHOW, handler_context=handler_context)

    context.api.assert_edit_message_called(update, collaborate_unavailable_view(user_with_settings.lang))


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
    assert view.description == CollaborateMessages.NOT_LINKED.get(
        lang=user_with_settings.lang,
        active_meetings=PATRON_ACTIVE_MEETINGS,
        scheduling_days=PATRON_SCHEDULING_DAYS,
    )
    assert "${" not in view.description.text
    link_button = view.keyboard[0][0]
    assert link_button.url.startswith("https://www.patreon.com/oauth2/authorize")
    state = parse_qs(urlparse(link_button.url).query)["state"][0]
    decoded = oauth.decode_state(patreon_config, state)
    assert decoded.tg_user_id == user_with_settings.tg_user_id
    # The tapped message id is threaded into the OAuth state so the web callback can refresh it.
    assert decoded.message_id == DEFAULT_MESSAGE_ID


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
            PATRON_ACTIVE_MEETINGS,
            PATRON_SCHEDULING_DAYS,
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.COLLABORATE)], indirect=True)
@pytest.mark.parametrize(
    "level,expected_message",
    [
        (SupporterLevel.SUPPORTER, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.PATRON, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.ORGANIZER, CollaborateMessages.LINKED_PATRON_ORGANIZER),
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


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.UNLINK_PATREON)], indirect=True)
async def test_unlink_deletes_subscription_and_revokes_premium(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    patreon_config: PatreonConfig,
):
    user_with_settings.supporter_level = SupporterLevel.PATRON
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
