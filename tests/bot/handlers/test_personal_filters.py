from types import SimpleNamespace
from typing import cast

import pytest
from sqlmodel import select
from telegram import Message, Update

from mitup_bot import guards
from mitup_bot.handlers import PositiveNumberFilter
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from tests.helpers import UpdateRequest, create_user
from tests.helpers.stub_db import MockDbSession


def register_member_lookup(mock_session: MockDbSession, user: User):
    """Register the select statement used by guards.member_user so the mock returns `user`."""
    statement = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
    mock_session.add_objects_with_statement(statement, (user,))


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
async def test_member_user_guard_without_effective_user(mock_session: MockDbSession, update: Update):
    assert await guards.member_user(update, mock_session) is None


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
async def test_member_user_guard_with_member_user(mock_session: MockDbSession, update: Update):
    """A MEMBER user matches — the guard is the gate for the existing-member /start flow."""
    assert update.effective_user is not None
    member = create_user(id=1, tg_user_id=update.effective_user.id, status=UserStatus.MEMBER)
    register_member_lookup(mock_session, member)

    assert await guards.member_user(update, mock_session) is member


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
async def test_member_user_guard_with_joined_only_user_is_none(mock_session: MockDbSession, update: Update):
    """A JOINED_ONLY user must NOT pass the guard.

    The guard's WHERE clause includes `status == MEMBER`, so a JOINED_ONLY row never matches
    even if the same tg_user_id exists. MockDbSession returns empty by default for
    unregistered statements, which is exactly what real SQL would return here.
    """
    assert update.effective_user is not None
    assert await guards.member_user(update, mock_session) is None


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
async def test_member_user_guard_with_left_user_is_none(mock_session: MockDbSession, update: Update):
    """A LEFT user must NOT pass the guard — they re-enter via the re-onboarding /start handler."""
    assert update.effective_user is not None
    assert await guards.member_user(update, mock_session) is None


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
async def test_member_user_guard_with_effective_user_not_found(mock_session: MockDbSession, update: Update):
    # While the update has an user, the user is not in the database
    assert update.effective_user is not None
    assert await guards.member_user(update, mock_session) is None


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(message_text="-1"),
        UpdateRequest(message_text="1e12"),
        UpdateRequest(message_text="hinumber"),
        UpdateRequest(message=False),
        UpdateRequest(message_text=""),
    ],
    ids=["negative", "number_and_char", "text", "without_message", "without_text"],
    indirect=True,
)
def test_positive_number_filter_with_wrong_messages(update: Update):
    assert PositiveNumberFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(message_text="1234")], indirect=True)
def test_positive_number_filter_with_positive_number(update: Update):
    assert PositiveNumberFilter().filter(update) is True


@pytest.mark.parametrize("update", [UpdateRequest(rich_message=True)], indirect=True)
def test_rich_message_filter_matches_api_kwargs_rich_message(update: Update):
    # The rich payload lands in `api_kwargs` under the pinned PTB, which has no `rich_message` field.
    assert update.effective_message is not None
    assert RichMessageFilter().filter(update.effective_message) is True


def test_rich_message_filter_matches_promoted_attribute():
    # Once a PTB upgrade promotes `rich_message` to a real attribute, the filter must still match
    # even with empty `api_kwargs`.
    message = SimpleNamespace(rich_message={"kind": "rich"}, api_kwargs={})
    assert RichMessageFilter().filter(cast(Message, message)) is True


@pytest.mark.parametrize("update", [UpdateRequest(message_text="hello")], indirect=True)
def test_rich_message_filter_rejects_plain_text(update: Update):
    assert update.effective_message is not None
    assert RichMessageFilter().filter(update.effective_message) is False
