from collections.abc import Iterator

import pytest
from telegram import Update
from telegram.ext import ChatJoinRequestHandler

from mitup_bot.exceptions import HandlerRegisteredError
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.hosts_group.enums import HostsGroupHandlerId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.hosts_group import HostsGroupState
from mitup_bot.supporter import SupporterLevel
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler, create_user
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
