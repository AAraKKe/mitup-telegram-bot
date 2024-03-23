from datetime import date
from unittest import mock

import pytest

from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import get_querys_from_session

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    date=date(2001, 1, 1),
)
COORDINATES = (123.1, -321.1)


@pytest.mark.parametrize("mock_meeting", [EXAMPLE_MEETING, None], ids=["meeting_exist", "meeting_does_not_exist"])
def test_meeting_does_not_exist(mock_session: mock.MagicMock, mock_meeting: mock.MagicMock):
    mock_session.exec.return_value.first.return_value = mock_meeting
    meeting = Meetup.by_id(mock_session, 1, must_exist=False)

    expected_query = get_querys_from_session(mock_session)[0]

    assert "WHERE meetups.id = 1" in expected_query

    assert meeting == mock_meeting


def test_meeting_does_not_exist_fail_when_must_exist(mock_session: mock.MagicMock):
    mock_session.exec.return_value.first.return_value = None

    with pytest.raises(MeetupNotFound):
        Meetup.by_id(mock_session, 1, must_exist=True)


@pytest.mark.parametrize(
    "name, expected_name",
    [
        (None, None),
        ("Central Park", "Central Park "),
        ("", None),
        (" ", None),
    ],
    ids=["name_not_set", "name_set", "name_empty", "name_space"],
)
@pytest.mark.parametrize(
    "coordinates, expected_coordinates",
    [
        (None, None),
        (COORDINATES, f"[{Emojis.PIN}]"),
    ],
    ids=["coordinates_not_set", "coordinates_set"],
)
def test_meetup_location_string_conversion(
    name: str | None,
    coordinates: tuple[float, float] | None,
    expected_name: str | None,
    expected_coordinates: str | None,
):
    location = MeetupLocation(name=name, coordinates=coordinates)

    if expected_coordinates is None and expected_name is None:
        expected = MeetingMessages.LOCATION_NOT_SET.get()
    else:
        expected = f"{expected_name or ''}{expected_coordinates or ''}"

    assert expected == str(location)
