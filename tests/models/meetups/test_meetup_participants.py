import datetime as dt

import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.models import JoinedUsers
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import participants_list_text
from tests.helpers import create_meetup, create_user


@pytest.mark.parametrize(
    "max_members,is_full", [[1, True], [2, False], [None, False]], ids=["full_not", "not_full", "not_full_no_limit"]
)
def test_meeting_is_full(max_members: int | None, is_full: bool):
    # A free owner is required: `full` now resolves capacity through the owner's premium status.
    # With the default cap (20) well above these limits, the effective cap equals `max_members`, so
    # the None case is still not full at one participant.
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=max_members)

    JoinedUsers(user=owner, meetup=meeting)
    assert meeting.full is is_full


@pytest.mark.parametrize("is_waiting_list", [True, False], ids=["waiting_list", "not_waiting_list"])
def test_create_joined_link(is_waiting_list: bool):
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user = create_user(id=2, first_name="Bob")
    joined_link = meeting.create_joined_link(user, is_waiting_list)

    assert joined_link.is_waiting_list == is_waiting_list
    assert joined_link.meetup == meeting
    assert joined_link.user == user
    assert joined_link in meeting.joined_links


def test_n_joined_counts_only_non_waiting_participants():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=True)

    assert meeting.n_participants == 2


def test_n_waiting_counts_only_waiting_participants():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=True)
    meeting.create_joined_link(user3, is_waiting_list=True)

    assert meeting.n_waiting == 2


def test_participants_returns_only_non_waiting_links():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=True)

    participants = meeting.participants

    assert len(participants) == 2
    assert link1 in participants
    assert link2 in participants


def test_participant_returns_specific_participant_by_user_id():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    participant = meeting.participant(2)

    assert participant is not None
    assert participant == link1
    assert participant.user == user1


@pytest.mark.parametrize(
    "is_public",
    [True, False],
    ids=["public_meeting", "private_meeting"],
)
def test_share_button_in_keyboard(is_public):
    meetup = create_meetup(123, "Meeting", public=is_public)
    keyboard = meeting_views.build_inline_keyboard(meetup)

    share_buttons = [btn for row in keyboard for btn in row if btn.switch_inline_query is not None]
    if is_public:
        assert len(share_buttons) == 1
        assert share_buttons[0].switch_inline_query == "123"
    else:
        assert len(share_buttons) == 0


def test_participant_returns_none_if_user_not_found():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=False)

    participant = meeting.participant(999)

    assert participant is None


def test_participant_returns_none_for_waiting_list_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=True)

    participant = meeting.participant(2)

    assert participant is None


def test_has_participant_returns_true_for_joined_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=False)

    assert meeting.has_participant(2)


def test_has_participant_returns_true_for_waiting_list_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=True)

    assert meeting.has_participant(2)


def test_has_participant_returns_false_for_non_participant():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)

    assert not meeting.has_participant(999)


def test_waiting_links_returns_sorted_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    meeting.create_joined_link(user2, is_waiting_list=False)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    waiting = meeting.waiting_links()

    assert len(waiting) == 2
    assert waiting[0] == link1
    assert waiting[1] == link3


@pytest.mark.parametrize(
    "max_members,waiting_list_enabled,expected",
    [
        (None, False, True),
        (None, True, True),
        (2, False, False),
        (2, True, True),
        (3, False, True),
    ],
    ids=["no_limit", "no_limit_with_waiting", "full_no_waiting", "full_with_waiting", "not_full"],
)
def test_join_allowed(max_members: int | None, waiting_list_enabled: bool, expected: bool):
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=max_members)
    meeting.waiting_list = waiting_list_enabled
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    assert meeting.join_allowed() == expected


def test_add_participant_adds_to_meeting_when_not_full():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=3)
    user1 = create_user(id=2, first_name="Bob")

    joined_link = meeting.add_participant(user1)

    assert joined_link is not None
    assert not joined_link.is_waiting_list
    assert joined_link.user == user1
    assert meeting.n_participants == 1


@pytest.mark.parametrize(
    "waiting_list_enabled, expected_is_waiting, expected_n_participants, expected_n_waiting",
    [
        (True, True, 1, 1),  # full + waiting list on  -> added to waiting list
        (False, None, 1, 0),  # full + waiting list off -> returns None
    ],
    ids=["full_waiting_list_enabled", "full_no_waiting_list"],
)
def test_add_participant_when_full(
    waiting_list_enabled: bool,
    expected_is_waiting: bool | None,
    expected_n_participants: int,
    expected_n_waiting: int,
):
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=1)
    meeting.waiting_list = waiting_list_enabled
    existing = create_user(id=2, first_name="Alice")
    meeting.create_joined_link(existing, is_waiting_list=False)
    candidate = create_user(id=3, first_name="Bob")

    joined_link = meeting.add_participant(candidate)

    if expected_is_waiting is None:
        assert joined_link is None
    else:
        assert joined_link is not None
        assert joined_link.is_waiting_list is expected_is_waiting
        assert joined_link.user == candidate

    assert meeting.n_participants == expected_n_participants
    assert meeting.n_waiting == expected_n_waiting


def test_remove_participant_removes_participant():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    link = meeting.create_joined_link(user1, is_waiting_list=False)
    promoted = meeting.remove_participant(link)

    assert link not in meeting.joined_links
    assert len(promoted) == 0


def test_remove_participant_promotes_from_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)

    promoted = meeting.remove_participant(link1)

    assert len(promoted) == 1
    assert promoted[0] == link2
    assert not link2.is_waiting_list
    assert meeting.n_participants == 1
    assert meeting.n_waiting == 0


def test_remove_participant_promotes_multiple_from_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=3, waiting_list=True)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")
    user4 = create_user(id=5, first_name="Diana")
    user5 = create_user(id=6, first_name="Ethan")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)
    link4 = meeting.create_joined_link(user4, is_waiting_list=True)
    link5 = meeting.create_joined_link(user5, is_waiting_list=True)

    promoted = meeting.remove_participant(link1)

    assert len(promoted) == 3
    assert promoted[0] == link2
    assert promoted[1] == link3
    assert promoted[2] == link4
    assert not link2.is_waiting_list
    assert not link3.is_waiting_list
    assert not link4.is_waiting_list
    assert link5.is_waiting_list
    assert meeting.n_participants == 3
    assert meeting.n_waiting == 1


def test_promote_from_waiting_list_promotes_all_when_no_limit():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 2
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert not link1.is_waiting_list
    assert not link2.is_waiting_list
    assert meeting.n_waiting == 0


def test_promote_from_waiting_list_promotes_up_to_max():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 2
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert not link1.is_waiting_list
    assert not link2.is_waiting_list
    assert link3.is_waiting_list
    assert meeting.n_waiting == 1
    assert meeting.n_participants == 2


def test_promote_from_waiting_list_returns_empty_when_no_waiting():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 0


def test_promote_from_waiting_list_respects_order():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    # Should promote in order they joined
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert link3.is_waiting_list


def test_participants_list_text_includes_waiting_list_section():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, max_members=3)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")
    user4 = create_user(id=5, first_name="Dave")

    # Add regular participants
    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=False)

    # Add waiting list participants
    meeting.create_joined_link(user4, is_waiting_list=True)

    expected = (
        f"\n  Bob"
        f"\n  Alice"
        f"\n  Charlie"
        f"\n--- {Emojis.WAITING} {ButtonMessages.WAITING_LIST.get(lang=meeting.lang).text} \n  "
        f"Dave"
    )

    assert expected == participants_list_text(meeting).text


def test_participants_list_text_without_waiting_list():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    # Add only regular participants
    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    expected = "\n  Bob\n  Alice"

    assert expected == participants_list_text(meeting).text


def test_participants_list_text_badges_supporter_participant():
    owner = create_user(id=1, first_name="Owner", tg_user_id=997_710)
    meeting = create_meetup(id=1, owner=owner)
    free = create_user(id=2, first_name="Bob", tg_user_id=997_711)
    supporter_member = create_user(id=3, username="alice", tg_user_id=997_712, supporter_level=SupporterLevel.HOST_1)

    meeting.create_joined_link(free, is_waiting_list=False)
    meeting.create_joined_link(supporter_member, is_waiting_list=False)

    # Only the supporter carries the badge (their tier's emoji); the free participant is untouched.
    expected = f"\n  Bob\n  {Emojis.HOST_1} alice"
    assert expected == participants_list_text(meeting).text


def test_waiting_links_orders_by_created_time_then_id():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    early = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    late = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)

    # Insertion order deliberately scrambled relative to the expected promotion order.
    newest = meeting.create_joined_link(create_user(id=4, first_name="Dana"), is_waiting_list=True)
    newest.created_time = late
    newest.id = 30
    legacy_second = meeting.create_joined_link(create_user(id=3, first_name="Bob"), is_waiting_list=True)
    legacy_second.created_time = early
    legacy_second.id = 20
    legacy_first = meeting.create_joined_link(create_user(id=2, first_name="Alice"), is_waiting_list=True)
    legacy_first.created_time = early
    legacy_first.id = 10

    # Pins #191's fairness contract: created_time first, then id — the tiebreaker that
    # keeps pre-fix rows (which share one process-start timestamp) in true join order.
    assert meeting.waiting_links() == [legacy_first, legacy_second, newest]
