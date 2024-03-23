import pytest
from telegram import Update

from mitup_bot.handlers import UserExistFilter
from tests.helpers import UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.parametrize("tg_update", [UpdateRequest(user=False)], indirect=True)
def test_user_exist_filter_without_effective_user(mock_session: MockDbSession, tg_update: Update):
    assert UserExistFilter().filter(tg_update) is False


@pytest.mark.parametrize("tg_update", [UpdateRequest(user=True)], indirect=True)
def test_user_exist_filter_with_effective_user(mock_session: MockDbSession, tg_update: Update):
    mock_session.add_user_from_update(tg_update)
    assert UserExistFilter().filter(tg_update) is True


@pytest.mark.parametrize("tg_update", [UpdateRequest(user=True)], indirect=True)
def test_user_exist_filter_with_effective_user_not_found(mock_session: MockDbSession, tg_update: Update):
    # While the update has an user, the user is not in the database
    assert tg_update.effective_user is not None
    assert UserExistFilter().filter(tg_update) is False
