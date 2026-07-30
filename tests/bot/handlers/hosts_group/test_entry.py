import logging
from collections.abc import Iterator

import pytest
from telegram import Update
from telegram.ext import ChatJoinRequestHandler

from mitup_bot.exceptions import HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.hosts_group.entry import GATE_EVENT, IGNORED_EVENT
from mitup_bot.handlers.hosts_group.enums import HostsGroupHandlerId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.hosts_group import HostsGroupState
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
from tests.helpers import HandlerContext, MockApi, MockDbSession, UpdateRequest, call_handler, create_user, log_record
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_USER_ID

OTHER_CHAT_ID = -1009999999999


@pytest.fixture(autouse=True)
def configured_hosts_group() -> Iterator[None]:
    """Enable the feature, pinning the hosts-group chat to the fixture's default chat so a join
    request built by the shared update fixture targets it. Each test overrides state as needed."""
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = DEFAULT_CHAT_ID
    HostsGroupState.invite_url = "https://t.me/+abc"
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_active_host_is_approved(mock_session: MockDbSession, handler_context: HandlerContext):
    """A known active host requesting to join is approved."""
    user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2)
    mock_session.add_user(user)

    context, _ = await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    context.api.assert_method_just_called("approve_chat_join_request", times=1)
    context.api.assert_method_just_called("decline_chat_join_request", times=0)
    assert context.api.call_args("approve_chat_join_request").kwargs == {
        "chat_id": DEFAULT_CHAT_ID,
        "tg_user_id": DEFAULT_USER_ID,
    }


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_non_host_is_declined(mock_session: MockDbSession, handler_context: HandlerContext):
    """A known user who is not a supporter is declined."""
    user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.NONE)
    mock_session.add_user(user)

    context, _ = await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    context.api.assert_method_just_called("decline_chat_join_request", times=1)
    context.api.assert_method_just_called("approve_chat_join_request", times=0)


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_unknown_user_is_declined(mock_session: MockDbSession, handler_context: HandlerContext):
    """A join request from a Telegram user with no linked account is declined."""
    # No user added to the session; lookup returns None.
    context, _ = await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    context.api.assert_method_just_called("decline_chat_join_request", times=1)
    context.api.assert_method_just_called("approve_chat_join_request", times=0)


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_request_for_other_chat_is_ignored(mock_session: MockDbSession, handler_context: HandlerContext):
    """A join request for any chat other than the hosts-only group is left untouched."""
    HostsGroupState.chat_id = OTHER_CHAT_ID
    user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2)
    mock_session.add_user(user)

    context, _ = await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    context.api.assert_method_just_called("approve_chat_join_request", times=0)
    context.api.assert_method_just_called("decline_chat_join_request", times=0)


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_feature_disabled_is_inert(mock_session: MockDbSession, handler_context: HandlerContext):
    """With no configured chat id the handler returns immediately: no approve or decline."""
    HostsGroupState.chat_id = None
    user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2)
    mock_session.add_user(user)

    context, _ = await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    context.api.assert_method_just_called("approve_chat_join_request", times=0)
    context.api.assert_method_just_called("decline_chat_join_request", times=0)


# --- the gate decision on the log plane ---
#
# Approve and decline share one event name, so the question the trail exists to answer — stranger
# vs. registered non-patron vs. lapsed host — is `stats count() by outcome, reason` rather than
# three indistinguishable lines.


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_approval_names_the_supporter_evidence_it_decided_on(
    mock_session: MockDbSession, handler_context: HandlerContext, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2)
    mock_session.add_user(user)

    await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    record = log_record(caplog, GATE_EVENT)
    assert record.levelname == "INFO"
    assert record.__dict__["outcome"] == "approved"
    assert record.__dict__["reason"] == "active_supporter"
    assert record.__dict__["applied"] is True
    assert record.__dict__["user_id"] == user.db_id
    assert record.__dict__["supporter_level"] == SupporterLevel.HOST_2.value
    assert record.__dict__["user_status"] == UserStatus.MEMBER.value


@pytest.mark.parametrize(
    ("registered", "expected_reason"),
    [pytest.param(True, "not_a_supporter", id="known"), pytest.param(False, "unknown_telegram_user", id="stranger")],
)
@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_decline_separates_a_stranger_from_a_registered_non_supporter(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    registered: bool,
    expected_reason: str,
):
    caplog.set_level(logging.INFO)
    if registered:
        mock_session.add_user(create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.NONE))

    await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    record = log_record(caplog, GATE_EVENT)
    assert record.__dict__["outcome"] == "declined"
    assert record.__dict__["reason"] == expected_reason
    assert record.__dict__["applied"] is True
    assert record.__dict__["supporter_level"] == (SupporterLevel.NONE.value if registered else None)


@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_a_refused_approval_is_not_reported_as_an_approval(
    mock_session: MockDbSession, handler_context: HandlerContext, caplog: pytest.LogCaptureFixture
):
    """Telegram swallows the refusal, so `applied` is the only thing separating a granted host from
    one the bot could not let in."""
    caplog.set_level(logging.INFO)
    mock_session.add_user(create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2))
    api = MockApi()
    api.register_on_method("approve_chat_join_request", return_value=False)

    await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context, api=api)

    record = log_record(caplog, GATE_EVENT)
    assert record.levelname == "WARNING"
    assert record.__dict__["outcome"] == "approve_failed"
    assert record.__dict__["applied"] is False
    # The decision and its evidence survive the delivery failure.
    assert record.__dict__["reason"] == "active_supporter"


@pytest.mark.parametrize(
    ("chat_id", "expected_reason"),
    [
        pytest.param(None, "hosts_group_not_configured", id="unconfigured"),
        pytest.param(OTHER_CHAT_ID, "other_chat", id="foreign_chat"),
    ],
)
@pytest.mark.parametrize("update", [UpdateRequest(chat_join_request=True)], indirect=True)
async def test_an_untouched_request_says_why_the_gate_stood_down(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    chat_id: int | None,
    expected_reason: str,
):
    """An inert gate and one seeing no traffic look identical without these lines."""
    caplog.set_level(logging.INFO)
    HostsGroupState.chat_id = chat_id
    mock_session.add_user(create_user(id=1, tg_user_id=DEFAULT_USER_ID, supporter_level=SupporterLevel.HOST_2))

    await call_handler(HostsGroupHandlerId.JOIN_REQUEST, handler_context=handler_context)

    record = log_record(caplog, IGNORED_EVENT)
    assert record.__dict__["outcome"] == "ignored"
    assert record.__dict__["reason"] == expected_reason


# --- registry wiring ---


def test_join_request_handler_registered():
    wrapper = HandlersRegistry.handlers[HostsGroupHandlerId.JOIN_REQUEST]

    assert isinstance(wrapper.handler, ChatJoinRequestHandler)
    assert wrapper.bindable is True


def test_register_chat_join_request_twice_raises():
    class DuplicateJoinRequestId(HandlerId):
        DUP = "dup_join_request"

    @HandlersRegistry.register_chat_join_request(handler_id=DuplicateJoinRequestId.DUP)
    async def first_handler(update: Update, context: object):
        return None

    try:
        with pytest.raises(HandlerRegisteredError):

            @HandlersRegistry.register_chat_join_request(handler_id=DuplicateJoinRequestId.DUP)
            async def second_handler(update: Update, context: object):
                return None
    finally:
        # Always restore the global registry, even if the assertion above fails.
        HandlersRegistry.handlers.pop(DuplicateJoinRequestId.DUP, None)
