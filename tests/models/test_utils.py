import pytest
from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.models import JoinedUsers, Settings, User, utils
from tests.helpers import UpdateRequest, create_meetup


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
