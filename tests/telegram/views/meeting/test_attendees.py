import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingCardSectionMessages, MeetingDisplayMessages
from mitup_bot.views import meeting as meeting_views
from tests.telegram.views.meeting.helpers import (
    OWNER_NAME,
    attendee_lines,
    body_lines,
    in_progress_meeting,
    owned_meeting,
    participants_header_line,
)


def chip_callbacks(meeting: Meetup) -> dict[str, str]:
    """The wire form of every attendance callback, keyed by name for readable assertions."""
    return {
        "join": str(cb.JOIN.with_id(meeting.db_id)),
        "invite": str(cb.INVITE.with_id(meeting.db_id)),
        "leave": str(cb.LEAVE.with_id(meeting.db_id)),
    }


def status_sentence(meeting: Meetup) -> str:
    """The membership sentence as a reader sees it, tags off."""
    return MeetingDisplayMessages.NOT_PART_OF_MEETING.rich(lang=meeting.user_language).text


def invite_chip_markup(meeting: Meetup) -> str:
    """The lone invite chip an owner already on the list is offered, as it is rendered."""
    label = ButtonMessages.INVITE.text(lang=meeting.user_language)
    return f'<tg-button type="callback_data" data="{cb.INVITE.with_id(meeting.db_id)}">{label}</tg-button>'


def participants_block(meeting: Meetup) -> list[str]:
    """The card's lines from the participants title down, which is where attendance is rendered."""
    lines = body_lines(meeting)
    return lines[lines.index(participants_header_line(meeting)) :]


# --- The counted title line, and the capacity chips under it ---


def test_the_count_rides_the_participants_title(lang: str):
    """The title names what is counted, so the count repeats no noun and takes no line of its own."""
    meeting = owned_meeting(lang=lang, guests=2, owner_joins=True)
    counted = MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=meeting.user_language, count=3, max=20)

    assert participants_block(meeting)[0].endswith(f" · {counted}")


def test_the_counted_title_keeps_the_incognito_glasses(lang: str):
    meeting = owned_meeting(lang=lang, guests=2, incognito=True)

    assert f" · {Emojis.GLASSES} " in participants_block(meeting)[0]


def test_the_capacity_chips_sit_directly_under_the_counted_title(lang: str):
    """With the count on the title, the chips that change capacity are the first row of the block."""
    meeting = owned_meeting(lang=lang, guests=2)

    chips_row = participants_block(meeting)[1]

    assert ButtonMessages.CHANGE_PARTICIPANT_LIMIT.text(lang=meeting.user_language) in chips_row
    assert ButtonMessages.MEETING_KICK_OUT.text(lang=meeting.user_language) in chips_row
    assert MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=meeting.user_language, count=2, max=20) not in chips_row


# --- Attendee list ---


def test_attendee_list_names_the_guests_one_per_line():
    meeting = owned_meeting(guests=3)

    lines = body_lines(meeting)

    assert [line for line in lines if line.startswith("Guest ")] == ["Guest 0", "Guest 1", "Guest 2"]


def test_attendee_list_names_guests_even_on_an_incognito_meeting():
    """Incognito withholds the list from everyone else; the owner still sees who is coming."""
    meeting = owned_meeting(guests=2, incognito=True)

    assert "Guest 0" in meeting_views.owner_view(meeting).message.text


def test_an_owner_who_joined_heads_the_list_with_a_way_out():
    """The owner's row is a name line like any other, first in the list and carrying Leave."""
    meeting = owned_meeting(guests=1, owner_joins=True)

    view = meeting_views.owner_view(meeting)

    assert attendee_lines(meeting)[0].startswith(OWNER_NAME)
    assert ButtonMessages.LEAVE.text(lang=meeting.user_language) in attendee_lines(meeting)[0]
    assert chip_callbacks(meeting)["leave"] in view.message.html


def test_the_owners_row_carries_no_marker_of_its_own():
    """Position in the list marks the owner; the name itself renders as every other name does,
    so a badge on it is the one the display name already carries."""
    meeting = owned_meeting(owner_joins=True)

    owner_row = attendee_lines(meeting)[0]
    leave_label = ButtonMessages.LEAVE.text(lang=meeting.user_language)

    assert owner_row == f"{OWNER_NAME} {leave_label}"


# --- The membership status line ---


def test_an_owner_with_no_link_reads_their_standing_under_the_header():
    """Hosting is not attending, so the card says so in words and hangs the chips off the
    sentence: the line sits between the header and the first name."""
    meeting = owned_meeting(guests=1, invitation=True)

    # The block runs title, count row, then the standing.
    block = participants_block(meeting)

    assert status_sentence(meeting) in block[2]
    assert block[3] == "Guest 0"
    assert ButtonMessages.JOIN.text(lang=meeting.user_language) in block[2]
    assert ButtonMessages.INVITE.text(lang=meeting.user_language) in block[2]


def test_the_status_line_carries_only_the_join_chip_when_invitations_are_off():
    meeting = owned_meeting(invitation=False)

    status_line = next(line for line in body_lines(meeting) if status_sentence(meeting) in line)

    assert ButtonMessages.JOIN.text(lang=meeting.user_language) in status_line
    assert ButtonMessages.INVITE.text(lang=meeting.user_language) not in status_line


@pytest.mark.parametrize("waits", [False, True], ids=["owner_joined", "owner_waitlisted"])
def test_an_owner_already_on_the_list_reads_no_status_line(waits: bool):
    """Their own row, or the waiting line, already answers where they stand."""
    meeting = owned_meeting(owner_joins=not waits, owner_waits=waits)

    assert status_sentence(meeting) not in meeting_views.owner_view(meeting).message.text


def test_a_locked_meeting_under_way_states_nothing_and_offers_nothing():
    """The attendee list is frozen while a locked meeting runs, so there is no standing to change
    and the sentence saying the owner could would be an offer the card cannot keep."""
    meeting = in_progress_meeting(locked=True, invitation=True, guests=1)

    view = meeting_views.owner_view(meeting)

    assert status_sentence(meeting) not in view.message.text
    assert not any(callback in view.message.html for callback in chip_callbacks(meeting).values())


def test_a_locked_meeting_under_way_takes_the_leave_chip_off_the_owners_row_too():
    meeting = in_progress_meeting(locked=True, invitation=True, guests=1, owner_joins=True)

    html = meeting_views.owner_view(meeting).message.html

    assert not any(callback in html for callback in chip_callbacks(meeting).values())


def test_an_unlocked_meeting_under_way_still_offers_the_way_in():
    meeting = in_progress_meeting(locked=False, invitation=True)

    view = meeting_views.owner_view(meeting)

    assert status_sentence(meeting) in view.message.text
    assert chip_callbacks(meeting)["join"] in view.message.html
    assert chip_callbacks(meeting)["invite"] in view.message.html


# --- The lone invite chip ---


@pytest.mark.parametrize("waits", [False, True], ids=["owner_joined", "owner_waitlisted"])
def test_an_owner_on_the_list_gets_the_invite_chip_below_the_names(waits: bool):
    """With no join left to offer, the invite stands alone under the list, which sets it clear of
    the last name with its own margin."""
    meeting = owned_meeting(guests=1, invitation=True, owner_joins=not waits, owner_waits=waits)

    html = meeting_views.owner_view(meeting).message.html
    chip = invite_chip_markup(meeting)

    assert chip in html
    assert html[: html.index(chip)].endswith("</ul>")


@pytest.mark.parametrize("waits", [False, True], ids=["owner_joined", "owner_waitlisted"])
def test_an_owner_on_the_list_is_offered_no_second_join(waits: bool):
    """A waitlisted owner has no join to make either: their way off the list is the Leave chip on
    the waiting line."""
    meeting = owned_meeting(invitation=True, owner_joins=not waits, owner_waits=waits)

    assert chip_callbacks(meeting)["join"] not in meeting_views.owner_view(meeting).message.html


def test_no_invite_chip_when_the_meeting_takes_no_invitations():
    meeting = owned_meeting(guests=1, owner_joins=True, invitation=False)

    assert chip_callbacks(meeting)["invite"] not in meeting_views.owner_view(meeting).message.html


# --- Waiting list ---


def test_the_waiting_list_names_its_members_under_its_counted_line(lang: str):
    meeting = owned_meeting(lang=lang, guests=1, waiting=3)

    lines = body_lines(meeting)
    waiting_lines = [line for line in lines if str(Emojis.WAITING) in line]

    assert len(waiting_lines) == 1
    assert ButtonMessages.WAITING_LIST.text(lang=lang) in waiting_lines[0]
    assert "3" in waiting_lines[0]
    assert "\nGuest 1\nGuest 2\nGuest 3\n" in meeting_views.owner_view(meeting).message.text


def test_a_meeting_with_nobody_waiting_shows_no_waiting_line():
    meeting = owned_meeting(guests=2)

    assert not any(str(Emojis.WAITING) in line for line in body_lines(meeting))


def test_a_waitlisted_owner_keeps_their_way_out_on_their_own_waiting_row():
    meeting = owned_meeting(guests=1, waiting=1, owner_waits=True)

    waiting_line = next(line for line in body_lines(meeting) if str(Emojis.WAITING) in line)
    html = meeting_views.owner_view(meeting).message.html

    # The chip hangs off the owner's own row in the waiting list, not off the counted line.
    assert ButtonMessages.LEAVE.text(lang=meeting.user_language) not in waiting_line
    assert f"<li>{OWNER_NAME} <tg-button" in html
    # Waiting is not attending, so the owner gets no row among the names either.
    assert attendee_lines(meeting) == ["Guest 0"]
