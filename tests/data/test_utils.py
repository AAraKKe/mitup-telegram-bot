import datetime as dt

import pytest
from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.models import JoinedUsers, Settings, User, utils
from tests.helpers import UpdateRequest, create_joined_link, create_meetup, create_user


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
def test_build_user(update: Update):
    user = utils.user_from_update(update)

    assert update.effective_user is not None
    assert user.tg_user_id == update.effective_user.id
    assert user.first_name == update.effective_user.first_name
    assert user.last_name == update.effective_user.last_name
    assert user.username == update.effective_user.username
    assert user.settings == Settings()


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_build_user_without_effective_user_raises(update: Update):
    with pytest.raises(EffectiveUserNotSet):
        utils.user_from_update(update)


@pytest.mark.parametrize(
    "max_members, full", [(1, True), (2, False), (None, False)], ids=["full", "not_full", "no_limit"]
)
@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list", "no_waiting_list"])
def test_build_joined_link(max_members: None | int, full: bool, waiting_list: bool):
    # Given a meeting with waiting list configured and its owner joined
    owner = User(first_name="Owner", tg_user_id=1)
    meeting = create_meetup(id=123, title="My Meeting", max_members=max_members, waiting_list=waiting_list, owner=owner)
    JoinedUsers(user=owner, meetup=meeting)

    # And a user that wants to join the meeting
    user = User(first_name="User", tg_user_id=2)
    joined_link = utils.joined_link(meeting, user)

    # Then the user should be able to join the meeting if it's not full or the waiting list is enabled
    expected_joined = (
        None if full and not waiting_list else JoinedUsers(user=user, meetup=meeting, is_waiting_list=full)
    )

    assert joined_link == expected_joined


def test_promote_from_waiting_list_promotes_users_when_meeting_not_full():
    # Given a meeting with max_members=3, waiting list enabled, and 1 participant + 1 on waiting list
    meeting = create_meetup(id=1, max_members=3, waiting_list=True)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    participant = create_user(id=2, tg_user_id=2)
    create_joined_link(participant, meeting, id=1, is_waiting_list=False)

    waiting_user = create_user(id=3, tg_user_id=3)
    waiting_link = create_joined_link(waiting_user, meeting, id=2, is_waiting_list=True)

    # When we promote from the waiting list
    promoted = utils.promote_from_waiting_list(meeting)

    # Then the waiting user should be promoted
    assert promoted == [waiting_link]
    assert waiting_link.is_waiting_list is False


def test_promote_from_waiting_list_no_promotion_when_meeting_still_full():
    # Given a meeting that is full (2 participants, max_members=2) with waiting list enabled
    meeting = create_meetup(id=1, max_members=2, waiting_list=True)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    p1 = create_user(id=2, tg_user_id=2)
    create_joined_link(p1, meeting, id=1, is_waiting_list=False)

    p2 = create_user(id=3, tg_user_id=3)
    create_joined_link(p2, meeting, id=2, is_waiting_list=False)

    waiting_user = create_user(id=4, tg_user_id=4)
    waiting_link = create_joined_link(waiting_user, meeting, id=3, is_waiting_list=True)

    # When we try to promote from the waiting list
    promoted = utils.promote_from_waiting_list(meeting)

    # Then no one should be promoted because the meeting is full
    assert promoted == []
    assert waiting_link.is_waiting_list is True


def test_promote_from_waiting_list_no_promotion_when_waiting_list_empty():
    # Given a meeting with space available but no one on the waiting list
    meeting = create_meetup(id=1, max_members=5, waiting_list=True)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    participant = create_user(id=2, tg_user_id=2)
    create_joined_link(participant, meeting, id=1, is_waiting_list=False)

    # When we try to promote from the waiting list
    promoted = utils.promote_from_waiting_list(meeting)

    # Then an empty list is returned
    assert promoted == []


def test_promote_from_waiting_list_promotes_multiple_users_up_to_available_spots():
    # Given a meeting with max_members=4, 2 participants, and 3 users on the waiting list
    # with staggered created_time values to ensure deterministic sort order
    meeting = create_meetup(id=1, max_members=4, waiting_list=True)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    p1 = create_user(id=2, tg_user_id=2)
    create_joined_link(p1, meeting, id=1, is_waiting_list=False)

    p2 = create_user(id=3, tg_user_id=3)
    create_joined_link(p2, meeting, id=2, is_waiting_list=False)

    w1 = create_user(id=4, tg_user_id=4)
    w1_link = create_joined_link(
        w1,
        meeting,
        id=3,
        is_waiting_list=True,
        created_time=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
    )

    w2 = create_user(id=5, tg_user_id=5)
    w2_link = create_joined_link(
        w2,
        meeting,
        id=4,
        is_waiting_list=True,
        created_time=dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
    )

    w3 = create_user(id=6, tg_user_id=6)
    w3_link = create_joined_link(
        w3,
        meeting,
        id=5,
        is_waiting_list=True,
        created_time=dt.datetime(2024, 1, 3, tzinfo=dt.UTC),
    )

    # When we promote from the waiting list (2 spots available: 4 max - 2 participants)
    promoted = utils.promote_from_waiting_list(meeting)

    # Then only 2 users should be promoted, in FIFO order
    assert len(promoted) == 2
    assert promoted == [w1_link, w2_link]
    assert w1_link.is_waiting_list is False
    assert w2_link.is_waiting_list is False
    # The third user remains on the waiting list
    assert w3_link.is_waiting_list is True


def test_promote_from_waiting_list_no_promotion_when_waiting_list_disabled():
    # Given a full meeting (max_members=1, 1 participant) with waiting_list=False and a user on
    # the waiting list — join_allowed() = not True or False = False, so promotion is blocked.
    # This isolates the waiting_list=False branch as the deciding factor.
    meeting = create_meetup(id=1, max_members=1, waiting_list=False)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    p1 = create_user(id=2, tg_user_id=2)
    create_joined_link(p1, meeting, id=1, is_waiting_list=False)

    waiting_user = create_user(id=3, tg_user_id=3)
    waiting_link = create_joined_link(waiting_user, meeting, id=2, is_waiting_list=True)

    # When we try to promote from the waiting list
    promoted = utils.promote_from_waiting_list(meeting)

    # Then no one should be promoted because waiting_list=False makes join_allowed()=False
    assert promoted == []
    assert waiting_link.is_waiting_list is True


def test_promote_from_waiting_list_raises_when_max_members_is_none():
    # Given a meeting with no member cap (max_members=None) and a user on the waiting list
    # The assert in promote_from_waiting_list guards against this invalid state
    meeting = create_meetup(id=1, max_members=None, waiting_list=True)
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting])

    waiting_user = create_user(id=2, tg_user_id=2)
    create_joined_link(waiting_user, meeting, id=1, is_waiting_list=True)

    # When we promote from the waiting list with no member cap
    with pytest.raises(AssertionError):
        utils.promote_from_waiting_list(meeting)
