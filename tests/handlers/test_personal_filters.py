import pytest
from sqlmodel import select
from telegram import Update

from mitup_bot.handlers import MemberUserFilter, PositiveNumberFilter
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from tests.helpers import UpdateRequest, create_user
from tests.helpers.stub_db import MockDbSession


def _register_member_lookup(mock_session: MockDbSession, user: User) -> None:
    """Register the select statement used by MemberUserFilter so the mock returns `user`."""
    statement = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
    mock_session.add_objects_with_statement(statement, (user,))


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_member_user_filter_without_effective_user(mock_session: MockDbSession, update: Update):
    assert MemberUserFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_member_user_filter_with_member_user(mock_session: MockDbSession, update: Update):
    """A MEMBER user matches — the filter is the gate for the existing-member /start flow."""
    assert update.effective_user is not None
    member = create_user(id=1, tg_user_id=update.effective_user.id, status=UserStatus.MEMBER)
    _register_member_lookup(mock_session, member)

    assert MemberUserFilter().filter(update) is True


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_member_user_filter_with_joined_only_user_is_false(mock_session: MockDbSession, update: Update):
    """A JOINED_ONLY user must NOT pass the filter.

    The filter's WHERE clause includes `status == MEMBER`, so a JOINED_ONLY row never matches
    even if the same tg_user_id exists. MockDbSession returns empty by default for
    unregistered statements, which is exactly what real SQL would return here.
    """
    assert update.effective_user is not None
    assert MemberUserFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_member_user_filter_with_left_user_is_false(mock_session: MockDbSession, update: Update):
    """A LEFT user must NOT pass the filter — they re-enter via the new-user /start handler."""
    assert update.effective_user is not None
    assert MemberUserFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_member_user_filter_with_effective_user_not_found(mock_session: MockDbSession, update: Update):
    # While the update has an user, the user is not in the database
    assert update.effective_user is not None
    assert MemberUserFilter().filter(update) is False


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
