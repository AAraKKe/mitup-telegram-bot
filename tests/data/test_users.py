import datetime as dt

import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.exceptions import UserNotFound
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
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


async def test_by_tg_user_id_rejects_participants_without_collections(mock_session: MockDbSession):
    """`load_participants` extends the collection load, so it is meaningless without it — the invalid
    combination is a caller bug and must fail loudly rather than silently skip the participant leaves."""
    with pytest.raises(ValueError, match="load_participants"):
        await User.by_tg_user_id(mock_session, tg_user_id=1, load_collections=False, load_participants=True)


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


def test_mark_inactive_stamps_left_time():
    """The LEFT grace period in user_cleanup is measured from this stamp."""
    user = create_user(id=4, tg_user_id=4, status=UserStatus.MEMBER)
    before = dt.datetime.now(dt.UTC)

    user.mark_inactive()

    assert user.left_time is not None
    assert before <= user.left_time <= dt.datetime.now(dt.UTC)


def test_mark_inactive_keeps_the_original_left_time_of_a_left_user():
    """Every failed delivery calls mark_inactive again; restamping would restart the grace period
    and keep a LEFT user whole forever."""
    stamped = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    user = create_user(id=5, tg_user_id=5, status=UserStatus.LEFT, left_time=stamped)

    user.mark_inactive()

    assert user.left_time == stamped


@pytest.mark.parametrize(
    "status",
    [UserStatus.MEMBER, UserStatus.JOINED_ONLY, UserStatus.DELETION_REQUESTED],
    ids=lambda status: status.value,
)
def test_set_status_clears_left_time_when_leaving_left(status: UserStatus):
    """`left_time` describes a LEFT user; carrying it into another status would make the grace
    period readable for someone who is no longer in it."""
    user = create_user(id=6, tg_user_id=6, status=UserStatus.LEFT, left_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))

    user.set_status(status)

    assert user.status is status
    assert user.left_time is None


def test_set_status_to_left_stamps_left_time():
    user = create_user(id=7, tg_user_id=7, status=UserStatus.JOINED_ONLY)

    user.set_status(UserStatus.LEFT)

    assert user.status is UserStatus.LEFT
    assert user.left_time is not None
