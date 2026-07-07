from unittest.mock import AsyncMock, MagicMock

import pytest

from mitup_bot.exceptions import UserNotFound
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import MitupView
from tests.helpers import MockDbSession, create_meetup, create_user


async def test_user_does_not_exist(mock_session: MockDbSession):
    user = await User.by_tg_user_id(mock_session, 1)

    assert user is None


async def test_user_exist(mock_session: MockDbSession, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await User.by_tg_user_id(mock_session, user_with_settings.tg_user_id)
    assert result == user_with_settings


async def test_by_tg_user_id_must_exist_raises_when_not_found(mock_session: MockDbSession):
    with pytest.raises(UserNotFound):
        await User.by_tg_user_id(mock_session, tg_user_id=999, must_exist=True)


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


def test_display_name_is_plain_inline_name_for_free_user():
    user = create_user(id=1, username="alice", tg_user_id=997_701)

    assert user.supporter_level is SupporterLevel.NONE
    assert user.display_name == "alice"


@pytest.mark.parametrize(
    "level,badge",
    [
        (SupporterLevel.HOST_1, Emojis.HOST_1),
        (SupporterLevel.HOST_2, Emojis.HOST_2),
        (SupporterLevel.HOST_3, Emojis.HOST_3),
    ],
    ids=["host_1", "host_2", "host_3"],
)
def test_display_name_prepends_per_tier_badge(level: SupporterLevel, badge: Emojis):
    user = create_user(id=1, username="alice", tg_user_id=997_701, supporter_level=level)

    assert user.display_name == f"{badge} alice"


@pytest.mark.parametrize(
    "meeting_id,expected_meeting",
    ([1, create_meetup(1)], [2, None]),
    ids=["user_has_meetup", "user_does_not_have_meetup"],
)
def test_own_meeting(meeting_id: int, expected_meeting: Meetup):
    user = User(first_name="Juan", tg_user_id=12345, meetups=[create_meetup(1), create_meetup(4)])

    meeting = user.own_meeting(meeting_id)

    assert expected_meeting == meeting


def test_mark_inactive_transitions_member_to_left_and_returns_true():
    """The metric path keys off the True return — only real transitions should flip it."""
    user = create_user(id=1, tg_user_id=1, status=UserStatus.MEMBER)

    transitioned = user.mark_inactive()

    assert transitioned is True
    assert user.status is UserStatus.LEFT


def test_mark_inactive_on_joined_only_is_a_noop_returning_false():
    """JOINED_ONLY users were never reachable; flipping them to LEFT would corrupt the model."""
    user = create_user(id=2, tg_user_id=2, status=UserStatus.JOINED_ONLY)

    transitioned = user.mark_inactive()

    assert transitioned is False
    assert user.status is UserStatus.JOINED_ONLY


def test_mark_inactive_on_left_is_a_noop_returning_false():
    """Re-running mark_inactive() on a LEFT user must not re-fire INACTIVE_USER_SET."""
    user = create_user(id=3, tg_user_id=3, status=UserStatus.LEFT)

    transitioned = user.mark_inactive()

    assert transitioned is False
    assert user.status is UserStatus.LEFT
