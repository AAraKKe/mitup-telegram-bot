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
