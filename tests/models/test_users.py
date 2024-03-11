from unittest import mock

import pytest

from mitup_bot.models import Meetup, User
from tests.helpers import get_querys_from_session


def create_meetup(
    id: int,
    title: str = "Default title",
    description="Default description",
) -> Meetup:
    return Meetup(id=id, title=title, description=description)


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


@pytest.mark.parametrize(
    "meeting_id,expected_meeting",
    ([1, create_meetup(1)], [2, None]),
    ids=["user_has_meetup", "user_does_not_have_meetup"],
)
def test_own_meeting(meeting_id: int, expected_meeting: Meetup):
    user = User(first_name="Juan", tg_user_id=12345, meetups=[create_meetup(1), create_meetup(4)])

    meeting = user.own_meeting(meeting_id)

    assert expected_meeting == meeting
