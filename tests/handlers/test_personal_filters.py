import pytest
from telegram import Update

from mitup_bot.handlers import UserExistFilter
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
