import datetime as dt
from typing import cast

from sqlmodel import Session, select
from telegram import Update

from mitup_bot.db import with_async_session
from mitup_bot.guards import user_language, valid_inline_query
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup, Message
from mitup_bot.monitoring import Feature
from mitup_bot.utils import InlineViewMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import MitupInlineView

from .enums import SEARCH_QUERY_PREFIX, InlineQueryId


def _sort_meetings(meetings: list[Meetup]) -> list[Meetup]:
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


@HandlersRegistry.register_inline_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, pattern=r"search:.+")
@with_async_session
async def search_chat_meetings(session: Session, update: Update, context: TMitupContext):
    """Search for meetings attached to a specific chat via ``chat_instance``.

    The inline query has the form ``search:<chat_instance>``.  The handler looks up
    all messages stored with that ``chat_instance``, collects the unique active
    meetings, sorts them by relevance, and returns them as inline results.
    """
    lang = user_language(update, session)
    query = valid_inline_query(update).query
    chat_instance = query.removeprefix(SEARCH_QUERY_PREFIX)

    statement = select(Message).where(Message.chat_instance == chat_instance)
    messages = session.exec(statement).all()

    # Collect unique active meetings via the already-loaded relationship
    seen: set[int] = set()
    meetings: list[Meetup] = []
    for message in messages:
        meeting = message.meetup
        if not meeting:
            continue
        if meeting.active and meeting.db_id not in seen:
            seen.add(meeting.db_id)
            meetings.append(meeting)

    if meetings:
        sorted_meetings = _sort_meetings(meetings)
        results = [meeting.inline_view(chat_instance=chat_instance) for meeting in sorted_meetings]
    else:
        results = [
            MitupInlineView(
                description=InlineViewMessages.NO_MEETINGS_FOUND_MESSAGE.get(lang=lang),
                keyboard=[],
                id="no_meetings_found",
                title=InlineViewMessages.NO_MEETINGS_FOUND_TITLE.get(lang=lang, plain=True),
                inline_description=InlineViewMessages.NO_MEETINGS_FOUND_DESCRIPTION.get(lang=lang, plain=True),
            ),
        ]

    await context.api.answer_inline_query(update=update, results=results, cache_time=0)
    context.put_feature_metric(Feature.SEARCH_CHAT_MEETINGS)
