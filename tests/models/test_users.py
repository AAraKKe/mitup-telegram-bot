from unittest import mock

from mitup_bot.models import User
from tests.helpers import get_querys_from_session


def test_user_does_not_exist(mock_session: mock.MagicMock):
    mock_session.exec.return_value.first.return_value = None
    user = User.by_tg_user_id(mock_session, 1)

    expected_query = get_querys_from_session(mock_session)[0]

    assert "WHERE users.tg_user_id = 1" in expected_query

    assert user is None


def test_user_exist(mock_session: mock.MagicMock):
    mock_user = mock.sentinel.user
    mock_session.exec.return_value.first.return_value = mock.sentinel.user

    user = User.by_tg_user_id(mock_session, 1)
    expected_query = get_querys_from_session(mock_session)[0]

    assert "WHERE users.tg_user_id = 1" in expected_query
    assert user == mock_user


def test_own_meeting(mock_session: mock.MagicMock):
    mock_session.exec.return_value.first.return_value = mock.sentinel.meeting
    user = User(id=1, first_name="john", tg_user_id=1)

    meeting = user.own_meeting(mock_session, 1)

    expected_query = get_querys_from_session(mock_session)[0]

    assert "WHERE meetups.id = 1 AND meetups.owner_id = 1" in expected_query
    assert meeting == mock.sentinel.meeting
