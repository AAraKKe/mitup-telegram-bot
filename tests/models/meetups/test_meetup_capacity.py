import pytest

from mitup_bot import limits
from mitup_bot.config import LimitsConfig
from mitup_bot.models import JoinedUsers, Meetup
from tests.helpers import create_meetup, create_user

# A small, explicit cap keeps the boundary and promotion setups readable; the enforcement is
# identical at the shipped default of 20.
CAP = 3


@pytest.fixture(autouse=True)
def pin_participant_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Pin the free-tier participant cap so the effective-cap behaviours are deterministic and no
    leaked config from another test can move the boundary."""
    monkeypatch.setattr(limits.LimitsState, "config", LimitsConfig(free_participant_capacity=CAP))
    return CAP


def add_participants(meeting: Meetup, count: int, *, waiting: bool, start_id: int) -> list[JoinedUsers]:
    """Attach `count` fresh users to `meeting`, returning the created links in creation order."""
    links = []
    # sourcery skip: no-loop-in-tests
    for idx in range(count):
        user = create_user(id=start_id + idx, first_name=f"User{start_id + idx}", tg_user_id=start_id + idx)
        links.append(meeting.create_joined_link(user, is_waiting_list=waiting))
    return links


@pytest.mark.parametrize(
    "is_premium,max_members,expected",
    [
        (False, None, CAP),  # free + no explicit limit resolves to the cap
        (False, 2, 2),  # free + explicit below cap is left untouched
        (False, 10, CAP),  # free + explicit above cap is clamped down (grandfathered)
        (True, None, None),  # premium + no explicit limit stays unlimited
        (True, 100, 100),  # premium + explicit limit is honored as-is
    ],
    ids=["free_no_limit", "free_below_cap", "free_above_cap", "premium_no_limit", "premium_explicit"],
)
def test_effective_max_members(is_premium: bool, max_members: int | None, expected: int | None):
    owner = create_user(id=1, first_name="Owner")
    owner.is_premium = is_premium
    meeting = create_meetup(id=1, owner=owner, max_members=max_members)

    assert meeting.effective_max_members == expected


@pytest.mark.parametrize("n_participants,expected_full", [(CAP, True), (CAP - 1, False)], ids=["at_cap", "below_cap"])
def test_full_free_no_limit_meeting_is_capped(n_participants: int, expected_full: bool):
    """A free owner's meeting with no explicit limit fills up at the cap instead of reading as
    unlimited."""
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, max_members=None)
    add_participants(meeting, n_participants, waiting=False, start_id=2)

    assert meeting.full is expected_full


def test_full_premium_no_limit_meeting_never_full():
    """A premium owner stays uncapped, so no participant count makes the meeting full."""
    owner = create_user(id=1, first_name="Owner")
    owner.is_premium = True
    meeting = create_meetup(id=1, owner=owner, max_members=None)
    add_participants(meeting, CAP + 3, waiting=False, start_id=2)

    assert meeting.full is False


def test_join_not_allowed_at_cap_without_waiting_list():
    """At the effective cap with no waiting list, joining is blocked even though max_members is None."""
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, max_members=None, waiting_list=False)
    add_participants(meeting, CAP, waiting=False, start_id=2)

    assert meeting.join_allowed() is False


def test_remove_participant_promotes_up_to_effective_cap():
    """Leaving frees exactly one seat, so promotion respects the effective cap rather than the
    absent max_members."""
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, max_members=None, waiting_list=True)
    joined = add_participants(meeting, CAP, waiting=False, start_id=2)
    waiting = add_participants(meeting, 2, waiting=True, start_id=20)

    promoted = meeting.remove_participant(joined[0])

    assert promoted == [waiting[0]]  # only one seat opened up
    assert meeting.n_participants == CAP
    assert meeting.n_waiting == 1


def test_grandfathered_over_cap_meeting_promotes_nobody_until_below_cap():
    """A meeting sitting above its effective cap (explicit limit clamped down) keeps everyone and
    promotes nobody on a leave until it drops back under the cap. Guards the regression where a
    negative promotion slice would otherwise be computed."""
    owner = create_user(id=1, first_name="Owner")
    # Free owner with an explicit limit above the cap: effective capacity is CAP, but the meeting is
    # seeded with one participant beyond it.
    meeting = create_meetup(id=1, owner=owner, max_members=10, waiting_list=True)
    participants = add_participants(meeting, CAP + 1, waiting=False, start_id=2)
    waiting = add_participants(meeting, 1, waiting=True, start_id=20)

    # Still at the cap after one leaves, so nobody is promoted and the waiting user stays put.
    promoted = meeting.remove_participant(participants[0])
    assert promoted == []
    assert meeting.n_participants == CAP
    assert waiting[0].is_waiting_list
    assert meeting.n_waiting == 1

    # Dropping below the cap lets the waiting user in.
    promoted_again = meeting.remove_participant(participants[1])
    assert promoted_again == [waiting[0]]
    assert not waiting[0].is_waiting_list
    assert meeting.n_participants == CAP
