"""Unit coverage of the Telegram-side linking write against a mock session.

The pending-link row lifecycle is covered in ``tests/patreon/test_pending_links.py``; what is
covered here is every branch of ``link_patreon_account``, which is the only place a subscription
row is written.
"""

from collections.abc import Iterator
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from structlog.typing import EventDict

from mitup_bot import patreon_link
from mitup_bot.hosts_group import HostsGroupState
from mitup_bot.models import SupporterSubscription
from mitup_bot.models.users import UserStatus
from mitup_bot.patreon_link import LinkOutcome, link_patreon_account, upsert_subscription
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages, SupporterNotificationMessages
from mitup_bot.views.collaborate import hosts_group_readmitted_view, link_confirmation_view
from tests.helpers import MockApi, create_supporter_subscription, create_user
from tests.helpers.stub_db import MockDbSession

HOSTS_GROUP_CHAT_ID = -1001234567890
HOSTS_GROUP_INVITE_URL = "https://t.me/+hostsonly"


def one_log(logs: list[EventDict], event: str) -> EventDict:
    """Return the single captured structlog entry whose event string matches ``event``."""
    matching = [entry for entry in logs if entry["event"] == event]
    assert len(matching) == 1, f"expected exactly one {event!r} log, got {len(matching)}"
    return matching[0]


def sent_views(api: MockApi) -> list[object]:
    """The views passed to every send_message_to_user call, so a test can check whether a specific
    DM (e.g. the readmission notice) was among them without counting the confirmation DM."""
    return [call.kwargs["view"] for call in api.call_args_list("send_message_to_user")]


@pytest.fixture
def reset_hosts_group() -> Iterator[None]:
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = None
    HostsGroupState.invite_url = None
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


# --- Linking ---


async def test_link_new_patron_grants_support():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_650)
    api = MockApi()

    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            session, api, user, patreon_user_id="p-650", granted_level=SupporterLevel.HOST_2
        )

    assert outcome is LinkOutcome.LINKED_SUPPORTER
    assert user.supporter_level is SupporterLevel.HOST_2
    added = [obj for obj in session.objects_added if isinstance(obj, SupporterSubscription)]
    assert len(added) == 1
    assert added[0].patreon_user_id == "p-650"
    assert added[0].support_expiration is not None
    api.assert_method_just_called("send_message_to_user", times=1)
    # Linking as an active Patron must DM the Patron unlock message specifically, proving the
    # redeemed tier is wired through unlocked_for. The DM carries a Main-menu button.
    api.assert_send_message_to_user_called(
        user=user,
        view=link_confirmation_view(SupporterNotificationMessages.PATRON_UNLOCKED.get(lang=user.lang), user.lang),
    )

    linked = one_log(logs, "Patreon account linked")
    assert linked["flow"] == "patreon_account_link"
    assert linked["stage"] == "persist"
    assert linked["outcome"] == "linked_supporter"
    assert linked["tg_user_id"] == 997_650
    assert linked["patreon_user_id"] == "p-650"
    assert linked["supporter_level"] == "host_2"


async def test_link_new_non_patron_stores_without_support():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_651)
    api = MockApi()

    outcome = await link_patreon_account(session, api, user, patreon_user_id="p-651", granted_level=SupporterLevel.NONE)

    assert outcome is LinkOutcome.LINKED_NO_PATRON
    assert user.supporter_level is SupporterLevel.NONE
    added = [obj for obj in session.objects_added if isinstance(obj, SupporterSubscription)]
    assert len(added) == 1
    assert added[0].support_expiration is None
    api.assert_method_just_called("send_message_to_user", times=1)


async def test_link_binds_to_the_user_it_is_given():
    # The whole security fix in one assertion: the subscription row is written against the user
    # handed to the function, which the handler takes from the sender of the redemption message.
    session = MockDbSession()
    redeemer = create_user(id=77, tg_user_id=997_677)
    api = MockApi()

    await link_patreon_account(session, api, redeemer, patreon_user_id="p-677", granted_level=SupporterLevel.HOST_2)

    added = next(obj for obj in session.objects_added if isinstance(obj, SupporterSubscription))
    assert added.user_id == redeemer.db_id
    assert api.call_args("send_message_to_user").kwargs["user"] is redeemer


async def test_confirmation_dm_carries_main_menu_button():
    # The confirmation DM must not strand the user on a button-less message: it carries the shared
    # link_confirmation_view, whose only row is a Main-menu button.
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_655)
    api = MockApi()

    await link_patreon_account(session, api, user, patreon_user_id="p-655", granted_level=SupporterLevel.NONE)

    view = api.call_args("send_message_to_user").kwargs["view"]
    assert view == link_confirmation_view(CollaborateMessages.LINK_CONFIRMED_NO_PATRON.get(lang=user.lang), user.lang)
    main_menu_button = view.keyboard[-1][0]
    assert main_menu_button.callback_data == cb.MAIN_MENU


async def test_link_rejected_when_account_claimed_elsewhere():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_652)
    # A subscription for the same Patreon account already belongs to a different user.
    other = create_supporter_subscription(user_id=2, patreon_user_id="p-shared")
    session.add_object(other, "patreon_user_id")
    api = MockApi()

    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            session, api, user, patreon_user_id="p-shared", granted_level=SupporterLevel.HOST_2
        )

    assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
    assert user.supporter_level is SupporterLevel.NONE
    assert not any(isinstance(obj, SupporterSubscription) for obj in session.objects_added)
    api.assert_method_just_called("send_message_to_user", times=0)

    warning = one_log(logs, "Patreon account already linked to another Telegram user")
    assert warning["flow"] == "patreon_account_link"
    assert warning["stage"] == "persist"
    assert warning["outcome"] == "already_linked_elsewhere"


async def test_relink_by_the_same_user_updates_in_place():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_653)
    existing = create_supporter_subscription(user_id=user.db_id, patreon_user_id="p-own")
    session.add_object(existing, "patreon_user_id")
    session.add_object(existing, "user_id")
    api = MockApi()

    outcome = await link_patreon_account(
        session, api, user, patreon_user_id="p-own", granted_level=SupporterLevel.HOST_1
    )

    assert outcome is LinkOutcome.LINKED_SUPPORTER
    # The row the user already had is the one that was updated; no second row was constructed.
    assert [obj for obj in session.objects_added if isinstance(obj, SupporterSubscription)] == [existing]


async def test_link_refused_for_a_user_pending_deletion():
    # A code minted before the account was marked can still be confirmed inside the mark-to-purge
    # window; the dying account must not gain a subscription row or a tier.
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_670, status=UserStatus.DELETION_REQUESTED)
    api = MockApi()

    with capture_logs(processors=[merge_contextvars]) as logs:
        outcome = await link_patreon_account(
            session, api, user, patreon_user_id="p-670", granted_level=SupporterLevel.HOST_2
        )

    assert outcome is LinkOutcome.PENDING_DELETION
    assert user.supporter_level is SupporterLevel.NONE
    assert not session.objects_added
    api.assert_method_just_called("send_message_to_user", times=0)

    warning = one_log(logs, "Patreon pairing code confirmed by a user pending deletion")
    assert warning["outcome"] == "pending_deletion"


async def test_link_maps_a_lost_uniqueness_race_to_already_linked_elsewhere():
    # The read-side check is a plain SELECT with no lock, so two confirmations for one Patreon
    # account can both pass it and let the loser hit the unique index. That is a friendly outcome,
    # not a crash. The real constraint behaviour is proven against Postgres; here the loss is
    # simulated by the upsert returning None, which is how racy_flush reports it.
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_671)
    api = MockApi()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch.object(patreon_link, "upsert_subscription", AsyncMock(return_value=None)):
            outcome = await link_patreon_account(
                session, api, user, patreon_user_id="p-671", granted_level=SupporterLevel.HOST_2
            )

    assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
    # Nothing on the user was touched before the race resolved, so there is nothing to undo.
    assert user.supporter_level is SupporterLevel.NONE
    api.assert_method_just_called("send_message_to_user", times=0)
    assert one_log(logs, "Patreon account was linked to another Telegram user concurrently")


async def test_upsert_creates_subscription_when_absent():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_653)

    subscription = await upsert_subscription(session, user, "p-653")

    assert subscription is not None
    assert subscription in session.objects_added
    assert subscription.user_id == user.db_id
    assert subscription.patreon_user_id == "p-653"


async def test_upsert_updates_in_place():
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_654)
    existing = create_supporter_subscription(user_id=user.db_id, patreon_user_id="p-old")
    session.add_object(existing, "user_id")

    result = await upsert_subscription(session, user, "p-654")

    # The same instance is mutated rather than replaced by a freshly constructed row.
    assert result is existing
    assert existing.patreon_user_id == "p-654"


# --- Hosts-only group re-admit on (re)link ---


async def test_link_supporter_readmits_banned_host_with_dm(reset_hosts_group: None):
    """A returning host who was banned is unbanned and sent the welcome-back view: the readmission
    copy with a Join button (the group is configured) plus a Main-menu button."""
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    HostsGroupState.invite_url = HOSTS_GROUP_INVITE_URL
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_660)
    api = MockApi()
    api.register_on_method("is_chat_banned", return_value=True)

    outcome = await link_patreon_account(
        session, api, user, patreon_user_id="p-660", granted_level=SupporterLevel.HOST_2
    )

    assert outcome is LinkOutcome.LINKED_SUPPORTER
    api.assert_method_just_called("unban_chat_member", times=1)
    assert api.call_args("unban_chat_member").kwargs == {
        "chat_id": HOSTS_GROUP_CHAT_ID,
        "tg_user_id": 997_660,
        "only_if_banned": True,
    }
    assert hosts_group_readmitted_view(user.lang, HOSTS_GROUP_INVITE_URL) in sent_views(api)


async def test_link_supporter_unbans_first_time_linker_without_dm(reset_hosts_group: None):
    """A first-time linker was never banned: the unban is idempotent and no readmission DM is sent."""
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_660)
    api = MockApi()

    # is_chat_banned defaults to False: the user was never in the group.
    outcome = await link_patreon_account(
        session, api, user, patreon_user_id="p-660", granted_level=SupporterLevel.HOST_2
    )

    assert outcome is LinkOutcome.LINKED_SUPPORTER
    api.assert_method_just_called("unban_chat_member", times=1)
    assert hosts_group_readmitted_view(user.lang, HostsGroupState.invite_url) not in sent_views(api)


async def test_link_non_patron_does_not_readmit(reset_hosts_group: None):
    HostsGroupState.chat_id = HOSTS_GROUP_CHAT_ID
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_661)
    api = MockApi()

    outcome = await link_patreon_account(session, api, user, patreon_user_id="p-661", granted_level=SupporterLevel.NONE)

    assert outcome is LinkOutcome.LINKED_NO_PATRON
    api.assert_method_just_called("unban_chat_member", times=0)


async def test_link_supporter_noop_when_hosts_group_unconfigured(reset_hosts_group: None):
    # reset_hosts_group leaves chat_id None: linking as a supporter never touches the group.
    session = MockDbSession()
    user = create_user(id=1, tg_user_id=997_662)
    api = MockApi()

    outcome = await link_patreon_account(
        session, api, user, patreon_user_id="p-662", granted_level=SupporterLevel.HOST_2
    )

    assert outcome is LinkOutcome.LINKED_SUPPORTER
    api.assert_method_just_called("unban_chat_member", times=0)
    api.mock_method("is_chat_banned").assert_not_called()
