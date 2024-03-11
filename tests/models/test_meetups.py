from datetime import date
from unittest import mock

import pytest

from mitup_bot.models import Meetup
from tests.helpers import get_querys_from_session

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    date=date(2001, 1, 1),
)


@pytest.mark.parametrize("mock_meeting", [EXAMPLE_MEETING, None], ids=["meeting_exist", "meeting_does_not_exist"])
def test_meeting_does_not_exist(mock_session: mock.MagicMock, mock_meeting: mock.MagicMock):
    mock_session.exec.return_value.first.return_value = mock_meeting
    meeting = Meetup.by_id(mock_session, 1)

    expected_query = get_querys_from_session(mock_session)[0]

    assert "WHERE meetups.id = 1" in expected_query

    assert meeting == mock_meeting


@pytest.mark.parametrize(
    "current_meeting", [EXAMPLE_MEETING, None], ids=["last_meeting_exist", "last_meeting_does_not_exist"]
)
@pytest.mark.skip(reason="This needs to be revisited before merging but need to clean up branch for now")
def test_return_last_meeting(mock_session: mock.MagicMock, current_meeting: Meetup | None):  # type: ignore
    mock_session.exec.return_value.first.return_value = current_meeting

    meeting = Meetup.get_last_from_user(mock_session, 1)
    expected_query = get_querys_from_session(mock_session)[0]

    assert "ORDER BY meetups.id DESC" in expected_query
    assert meeting == current_meeting
