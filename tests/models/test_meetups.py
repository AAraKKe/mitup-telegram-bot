from datetime import date, time
from unittest import mock

import pytest

from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.models import Meetup, MeetupLocation, Settings, User
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.messages import MeetingMessages
from tests.stub_db import MockDbSession

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    date=date(2001, 1, 1),
)
COORDINATES = (123.1, -321.1)


def expected_location_name(expected_name, expected_coordinates):
    return (
        MeetingMessages.LOCATION_NOT_SET.get()
        if expected_coordinates is None and expected_name is None
        else f"{expected_name or ''} {expected_coordinates or ''}".strip()
    )


def expected_participants_message(max_participants: bool) -> str:
    total_participants = MeetingMessages.EMPTY.get()
    max_participants_text = "(Max: 5)" if max_participants else ""

    return f"{total_participants} {max_participants_text}"


def expected_message(
    description: bool,
    date: bool,
    username: bool,
    location_name: bool,
    coordinates: bool,
    max_participants: bool,
) -> str:
    str_description = "Test Description" if description else MeetingMessages.DESCRIPTION_NOT_SET.get()
    str_date = "1987-07-17 01:59 (Europe/Madrid)" if date else MeetingMessages.DATE_NOT_SET.get()
    owner = "john_doe" if username else "John"
    location = expected_location_name("Test Location" if location_name else None, "[📍]" if coordinates else None)
    str_participants = expected_participants_message(max_participants)
    return MeetingMessages.FEATURES.get(
        title="Test Meeting",
        owner=owner,
        description=str_description,
        date=str_date,
        location=location,
        participants=str_participants,
    )


@pytest.mark.parametrize("mock_meeting", [EXAMPLE_MEETING, None], ids=["meeting_exist", "meeting_does_not_exist"])
def test_meeting_does_not_exist(mock_session: MockDbSession, mock_meeting: mock.MagicMock):
    mock_session.add_object(mock_meeting)
    meeting = Meetup.by_id(mock_session, 123, must_exist=False)

    expected_query = mock_session.queries_executed[0]

    assert "WHERE meetups.id = 1" in expected_query

    assert meeting == mock_meeting


def test_meeting_does_not_exist_fail_when_must_exist(mock_session: MockDbSession):
    with pytest.raises(MeetupNotFound):
        Meetup.by_id(mock_session, 1, must_exist=True)


@pytest.mark.parametrize(
    "name, expected_name",
    [
        (None, None),
        ("Central Park", "Central Park"),
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

    expected = expected_location_name(expected_name, expected_coordinates)

    assert expected == str(location)


@pytest.mark.parametrize(
    "description, meetup_date, username, location_name, location_coordinates, max_participants",
    [
        (False, True, True, True, True, True),
        (True, False, True, True, True, True),
        (True, True, False, True, True, True),
        (True, True, True, False, False, True),
        (True, True, True, True, True, False),
        (True, True, True, False, True, True),
        (True, True, True, True, False, True),
        (True, True, True, True, True, True),
    ],
    ids=[
        "no_description",
        "no_date",
        "no_username",
        "no_location",
        "no_max_members",
        "with_location_coordinates",
        "with_location_name",
        "all_fields",
    ],
)
def test_meetup_features_message(
    settings: Settings,
    description: bool,
    meetup_date: bool,
    username: bool,
    location_name: bool,
    location_coordinates: bool,
    max_participants: bool,
):
    location = MeetupLocation(
        name="Test Location" if location_name else None,
        coordinates=COORDINATES if location_coordinates else None,
    )
    meeting = Meetup(
        title="Test Meeting",
        description="Test Description" if description else None,
        date=date(1987, 7, 16) if meetup_date else None,
        time=time(23, 59) if meetup_date else None,
        location=location,
        max_members=5 if max_participants else None,
        owner=User(first_name="John", username="john_doe" if username else None, tg_user_id=1, settings=settings),
    )

    expected = expected_message(
        description, meetup_date, username, location_name, location_coordinates, max_participants
    )

    assert expected == meeting.message


def test_time_properly_converted_for_timezone(settings: Settings):
    meeting = Meetup(
        title="Test Meeting",
        description="Test Description",
        date=date(2024, 1, 12),
        time=time(12, 30),
        owner=User(first_name="John", username="john_doe", tg_user_id=1, settings=settings),
    )

    # Expected time is 1 hour ahead of the one in the meeting (in UTC) assuming converstion to settings.tz
    # Europe/Madrid
    expected_time = "2024-01-12 13:30 (Europe/Madrid)"

    assert expected_time == meeting.str_date

    # Updatingt he timezone to Europe/Dublin in January the date should be the same but the time should be 12:30
    # since Dublin is in the same timezone as UTC
    settings.timezone = "Europe/Dublin"
    expected_time = "2024-01-12 12:30 (Europe/Dublin)"
    assert expected_time == meeting.str_date
