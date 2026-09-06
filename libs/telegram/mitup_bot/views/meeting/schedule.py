from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views.datetime_format import datetime_content, datetime_range_content

if TYPE_CHECKING:
    from mitup_bot.models import Meetup


def schedule_content(meeting: Meetup, start: dt.datetime, end: dt.datetime | None) -> RichContent:
    """*start*, or the span from *start* to *end*, written in the meeting's own language, timezone
    and time format."""
    if end is None:
        return datetime_content(
            start, lang=meeting.lang, tz=meeting.timezone, created=meeting.created_time, time_format=meeting.time_format
        )
    return datetime_range_content(
        start,
        end,
        lang=meeting.lang,
        tz=meeting.timezone,
        created=meeting.created_time,
        time_format=meeting.time_format,
    )
