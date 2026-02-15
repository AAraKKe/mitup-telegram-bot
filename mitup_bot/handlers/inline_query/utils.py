import datetime as dt
from typing import cast

from mitup_bot.models import Meetup


def sort_meetings(meetings: list[Meetup]) -> list[Meetup]:
    """Sort meetings by relevance: future first, then no datetime, then past."""
    now = dt.datetime.now(tz=dt.UTC)

    future: list[Meetup] = []
    no_datetime: list[Meetup] = []
    past: list[Meetup] = []

    for meeting in meetings:
        if meeting.datetime is None:
            no_datetime.append(meeting)
        elif meeting.datetime >= now:
            future.append(meeting)
        else:
            past.append(meeting)

    future.sort(key=lambda m: cast(dt.datetime, m.datetime))
    no_datetime.sort(key=lambda m: cast(dt.datetime, m.created_time))
    past.sort(key=lambda m: cast(dt.datetime, m.datetime))

    return [*future, *no_datetime, *past]
