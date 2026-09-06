from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.utils import (
    Emojis,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
)
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.utils.rich_template import render_rich, render_rich_tags
from mitup_bot.views.datetime_format import localized_datetime

if TYPE_CHECKING:
    from mitup_bot.models import JoinedUsers, Meetup


def title_content(meeting: Meetup) -> RichContent:
    """The stored title as rich content, for substituting into a message built with `rich`."""
    return render_rich_tags(meeting.tagged_title, field="title")


def description_content(meeting: Meetup) -> RichContent | None:
    """The stored description as rich content; None mirrors an unset or empty description so
    callers keep their placeholder branches."""
    if not (tagged := meeting.tagged_description):
        return None
    return render_rich_tags(tagged, field="description")


def participant_name(link: JoinedUsers) -> RichContent:
    """One attendee as a card composes them: their name, trailing the invitation that brought them."""
    name = link.user.display_name
    if link.invited_by is not None:
        language = link.meetup.lang
        invited_by = MeetingDisplayMessages.INVITED_BY.rich(lang=language, user=link.invited_by.inline_name)
        return render_rich(t"{name} ({invited_by})")
    return RichContent(name)


def plain_datetime(meeting: Meetup) -> str:
    """The meeting's start written out in its own language and timezone, for inline query previews.

    An inline query result description is a bare string, so this is one of the surfaces where a
    `date_time` entity cannot be attached at all.
    """
    if meeting.datetime:
        return localized_datetime(
            meeting.datetime,
            lang=meeting.lang,
            tz=meeting.timezone,
            created=meeting.created_time,
            time_format=meeting.time_format,
        )
    return MeetingDisplayMessages.DATE_NOT_SET.text(lang=meeting.lang)


def participants_badge(meeting: Meetup) -> str:
    """Plain-text badge shown in inline query result descriptions."""
    empty = MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=meeting.lang)
    joined_count = len(meeting.joined_links)
    no_limit = f"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.text(lang=meeting.user_language)})"

    incognito_prefix = f"{Emojis.GLASSES} " if meeting.incognito else ""

    cap = meeting.effective_max_members
    if cap is None:
        result_badged = empty if joined_count == 0 else f"{joined_count} {no_limit}"
        return f"{incognito_prefix}{result_badged}"

    max_label = MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.text(lang=meeting.lang, max_participants=cap)
    result_badged = f"{empty} {max_label}" if joined_count == 0 else f"({joined_count}/{cap})"
    return f"{incognito_prefix}{result_badged}"


def inline_query_message(meeting: Meetup) -> str:
    """Plain-text preview shown below the title in inline query results."""
    result = f"{Emojis.JOINED} {participants_badge(meeting)}"

    if meeting.datetime:
        return f"{result}\n{Emojis.CLOCK} {plain_datetime(meeting)}"

    return result
