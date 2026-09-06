from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mitup_bot.utils import (
    ButtonMessages,
    Emojis,
    MeetingCardSectionMessages,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
)
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.meeting_text import participant_name

if TYPE_CHECKING:
    from mitup_bot.models import JoinedUsers, Meetup


def participants_count_line(meeting: Meetup) -> RichContent:
    """The count and cap as bare numbers: the Participants title it rides already names what is
    being counted. A meeting nobody has joined says so in a word rather than with a zero, and names
    its capacity anyway, so the count reads as an invitation instead of as a defect.

    The count covers the waiting list, so a meeting whose cap is reached still reads as bigger than
    its cap rather than as stuck at it.
    """
    prefix = f"{Emojis.GLASSES} " if meeting.incognito else ""
    count = len(meeting.joined_links)
    empty = MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=meeting.lang)
    cap = meeting.effective_max_members
    if cap is None:
        no_limit = MeetingEditParticipantsMessages.NO_LIMIT_LABEL.text(lang=meeting.lang)
        return RichContent(f"{prefix}{count or empty} ({no_limit})")
    if not count:
        max_label = MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.text(lang=meeting.lang, max_participants=cap)
        return RichContent(f"{prefix}{empty} {max_label}")
    counted = MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=meeting.lang, count=count, max=cap)
    return RichContent(f"{prefix}{counted}")


def waiting_list_line(meeting: Meetup, lang: str) -> RichContent:
    """The counted line opening the waiting list block."""
    label = ButtonMessages.WAITING_LIST.rich(lang=lang)
    return render_rich(t"{Emojis.WAITING} {label} · {meeting.n_waiting}")


def named_items(
    links: list[JoinedUsers], shown: int | None, lang: str, row: Callable[[JoinedUsers], RichContent] = participant_name
) -> list[RichContent]:
    """The first *shown* links as list items rendered by *row*, closing on an item counting the
    rest. None names everyone."""
    named = links if shown is None else links[:shown]
    items = [row(link) for link in named]
    if hidden := len(links) - len(named):
        items.append(MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.rich(lang=lang, count=hidden))
    return items
