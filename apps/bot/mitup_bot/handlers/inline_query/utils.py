import datetime as dt
from typing import cast

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.utils import ButtonMessages

from .enums import SEARCH_QUERY_PREFIX


def search_chat_meetings_button(*, lang: str, chat_instance: str) -> ButtonConfig:
    """Button that re-invokes the inline search for the current chat.

    Shared by every step of the guided search flow so the user always has an
    explicit way to (re)start the search without manually typing `@bot` — this
    includes the zero-results message, which would otherwise be a dead end.
    """
    return ButtonConfig(
        text=ButtonMessages.SEARCH_CHAT_MEETINGS.get_text(lang=lang),
        switch_inline_query_current_chat=f"{SEARCH_QUERY_PREFIX}{chat_instance}",
    )


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
