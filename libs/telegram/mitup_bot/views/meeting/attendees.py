from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, button_content, unordered_list_content
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.meeting.controls import meeting_chip
from mitup_bot.views.meeting.sections import named_items, waiting_list_line
from mitup_bot.views.meeting_text import participant_name

if TYPE_CHECKING:
    from mitup_bot.models import JoinedUsers, Meetup


def leave_chip(meeting: Meetup) -> ButtonConfig | None:
    """The owner's way out of their own meeting, or None while the list is frozen."""
    if not meeting.attendance_is_open:
        return None
    return meeting_chip(ButtonMessages.LEAVE, meeting, cb.LEAVE, style="danger")


def editor_participants_chips(meeting: Meetup) -> RichContent:
    """The capacity chip, the remove chip when an explicit limit is set, and, once anyone besides
    the owner has joined, the kick-out chip.

    The section title above carries the glyph and the count for the whole block, so this row
    carries neither.
    """
    chips = [
        meeting_chip(
            ButtonMessages.CHANGE_PARTICIPANT_LIMIT
            if meeting.effective_max_members is not None
            else ButtonMessages.SET_PARTICIPANT_LIMIT,
            meeting,
            cb.EDIT_MEETING_MAX_PARTICIPANTS,
        )
    ]
    # The remove chip needs a set limit to remove: a capped owner with no explicit limit already
    # sits at the plan's cap, which is not removable.
    if meeting.max_members is not None:
        chips.append(meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_LIMIT, style="danger"))
    if any(link.user_id != meeting.owner_id for link in meeting.joined_links):
        chips.append(
            ButtonConfig(
                text=ButtonMessages.MEETING_KICK_OUT.text(lang=meeting.user_language),
                callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(meeting_id=meeting.db_id, id=1),
            )
        )
    return RichContent.join(" ", [button_content(chip) for chip in chips])


def owner_attendee_lines(meeting: Meetup) -> list[RichContent]:
    """The owner's own row at the head of the list, when they are attending their meeting rather
    than only hosting it.

    The name is rendered exactly as every other attendee's is, badge and all, so what marks this
    row is its position and the chip that takes them back out, not decoration of its own.
    """
    attendance = meeting.owner_attendance
    if attendance is None or attendance.is_waiting_list:
        return []
    name = participant_name(attendance)
    leave = leave_chip(meeting)
    if leave is None:
        return [name]
    return [render_rich(t"{name} {leave}")]


def guest_lines(meeting: Meetup, shown: int | None) -> list[RichContent]:
    """One item per named guest, closing on an item counting the ones left out.

    *shown* caps how many are named; None names everyone. Names are listed even on an incognito
    meeting: incognito hides the list from the surfaces everyone else sees, and this is the owner's
    own card. The owner's row is built apart, so it is never what a fitted card gives up.
    """
    return named_items(meeting.guest_links, shown, meeting.user_language)


def waiting_rows(meeting: Meetup, shown: int | None) -> list[RichContent]:
    """The waiting members as items in promotion order, the owner's own row carrying their way off
    the list."""

    def row(link: JoinedUsers) -> RichContent:
        name = participant_name(link)
        if link.user_id == meeting.owner_id and (leave := leave_chip(meeting)) is not None:
            return render_rich(t"{name} {leave}")
        return name

    return named_items(meeting.waiting_links(), shown, meeting.user_language, row)


def membership_status_line(meeting: Meetup) -> RichContent:
    """Where the owner stands on their own meeting, and the chips that change it.

    Hosting a meeting is not attending it, so an owner who never joined is told so in words. The
    sentence is what the join and invite chips hang off: on their own under the attendee list they
    read as a stray control, and beside a line saying the owner is not on the list they read as
    the answer to it.
    """
    status = MeetingDisplayMessages.NOT_PART_OF_MEETING.rich(lang=meeting.user_language)
    chips = [meeting_chip(ButtonMessages.JOIN, meeting, cb.JOIN)]
    if meeting.allow_invitation:
        chips.append(meeting_chip(ButtonMessages.INVITE, meeting, cb.INVITE))
    chip_row = RichContent.join(" ", [button_content(chip) for chip in chips])
    return render_rich(t"{status} {chip_row}")


def attendee_section(meeting: Meetup, shown: int | None = None) -> RichContent:
    """What sits under the counted Participants title: the capacity chips, the owner's standing on
    the meeting, the confirmed attendees as a bullet list, and the waiting list as one line.

    An owner with no link of their own reads their standing as a sentence directly under the
    chips. One already on the list has that sentence answered by their own row or by the waiting
    line, so all that is left to offer them is the invite, which sits below the names.

    *shown* caps how many guests are named, replacing the rest with an item counting them.
    """
    attendance = meeting.owner_attendance
    head = [editor_participants_chips(meeting)]
    if attendance is None and meeting.attendance_is_open:
        head.append(membership_status_line(meeting))
    section = RichContent.join("\n", head)

    rows = [*owner_attendee_lines(meeting), *guest_lines(meeting, shown)]
    tail = []
    if attendance is not None and meeting.attendance_is_open and meeting.allow_invitation:
        tail.append(button_content(meeting_chip(ButtonMessages.INVITE, meeting, cb.INVITE)))
    if meeting.n_waiting:
        waiting = waiting_list_line(meeting, meeting.user_language)
        tail.append(waiting.append(unordered_list_content(waiting_rows(meeting, shown))))

    if not rows:
        return RichContent.join("\n\n", [section, *tail])
    # The list block draws its own margins, so nothing around it takes an authored blank line.
    section = section.append(unordered_list_content(rows))
    return section.append(RichContent.join("\n\n", tail))
