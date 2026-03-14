from unittest.mock import AsyncMock, MagicMock

import pytest

from mitup_bot.exceptions import UserNotFound
from mitup_bot.models import Meetup, User
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import MitupView
from tests.helpers import MockDbSession, create_meetup


def test_user_does_not_exist(mock_session: MockDbSession):
    user = User.by_tg_user_id(mock_session, 1)

    assert user is None


def test_user_exist(mock_session: MockDbSession, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = User.by_tg_user_id(mock_session, user_with_settings.tg_user_id)
    assert result == user_with_settings


def test_by_tg_user_id_must_exist_raises_when_not_found(mock_session: MockDbSession):
    with pytest.raises(UserNotFound):
        User.by_tg_user_id(mock_session, tg_user_id=999, must_exist=True)


async def test_send_message_calls_bot_with_correct_args():
    user = User(first_name="Alice", tg_user_id=42)
    view = MitupView(
        description=FormattedText("Hello"),
        keyboard=[],
    )

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    await user.send_message(mock_bot, view)

    mock_bot.send_message.assert_called_once_with(
        chat_id=42,  # user.tg_user_id
        text="Hello",
        entities=None,  # no entities in plain FormattedText, entities list is empty → None
        reply_markup=None,  # empty keyboard → markup is None
    )


@pytest.mark.parametrize(
    "meeting_id,expected_meeting",
    ([1, create_meetup(1)], [2, None]),
    ids=["user_has_meetup", "user_does_not_have_meetup"],
)
def test_own_meeting(meeting_id: int, expected_meeting: Meetup):
    user = User(first_name="Juan", tg_user_id=12345, meetups=[create_meetup(1), create_meetup(4)])

    meeting = user.own_meeting(meeting_id)

    assert expected_meeting == meeting
