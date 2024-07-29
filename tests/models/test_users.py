import pytest

from mitup_bot.models import Meetup, User
from tests.helpers.stub_db import MockDbSession


def create_meetup(
    id: int,
    title: str = "Default title",
    description="Default description",
) -> Meetup:
    return Meetup(id=id, title=title, description=description)


def test_user_does_not_exist(mock_session: MockDbSession):
    user = User.by_tg_user_id(mock_session, 1)

    assert user is None


def test_user_exist(mock_session: MockDbSession, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = User.by_tg_user_id(mock_session, user_with_settings.tg_user_id)
    assert result == user_with_settings


@pytest.mark.parametrize(
    "meeting_id,expected_meeting",
    ([1, create_meetup(1)], [2, None]),
    ids=["user_has_meetup", "user_does_not_have_meetup"],
)
def test_own_meeting(meeting_id: int, expected_meeting: Meetup):
    user = User(first_name="Juan", tg_user_id=12345, meetups=[create_meetup(1), create_meetup(4)])

    meeting = user.own_meeting(meeting_id)

    assert expected_meeting == meeting
