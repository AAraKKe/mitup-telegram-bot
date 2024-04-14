import pytest
from telegram import Update

from mitup_bot.handlers import PositiveNumberFilter, UserExistFilter
from tests.helpers import UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_user_exist_filter_without_effective_user(mock_session: MockDbSession, update: Update):
    assert UserExistFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_user_exist_filter_with_effective_user(mock_session: MockDbSession, update: Update):
    mock_session.add_user_from_update(update)
    assert UserExistFilter().filter(update) is True


@pytest.mark.parametrize("update", [UpdateRequest(user=True)], indirect=True)
def test_user_exist_filter_with_effective_user_not_found(mock_session: MockDbSession, update: Update):
    # While the update has an user, the user is not in the database
    assert update.effective_user is not None
    assert UserExistFilter().filter(update) is False


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(message="-1"),
        UpdateRequest(message="1e12"),
        UpdateRequest(message="hinumber"),
        UpdateRequest(message=False),
        UpdateRequest(message=""),
    ],
    ids=["negative", "number_and_char", "text", "without_message", "without_text"],
    indirect=True,
)
def test_positive_number_filter_with_wrong_messages(update: Update):
    assert PositiveNumberFilter().filter(update) is False


@pytest.mark.parametrize("update", [UpdateRequest(message="1234")], indirect=True)
def test_positive_number_filter_with_positive_number(update: Update):
    assert PositiveNumberFilter().filter(update) is True
