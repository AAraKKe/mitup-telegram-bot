from typing import Any, cast

import structlog
from sqlalchemy.orm import QueryableAttribute, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.guards import user_language, valid_inline_query
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message
from mitup_bot.monitoring import Feature
from mitup_bot.utils import InlineQueryMessages
from mitup_bot.views import MitupInlineView
from mitup_bot.views import meeting as meeting_views

from .enums import SEARCH_QUERY_PREFIX, InlineQueryId
from .utils import search_chat_meetings_button, sort_meetings

log = structlog.get_logger(__name__)


@HandlersRegistry.register_inline_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, pattern=r"search:.+")
@with_session
async def search_chat_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    """Search for meetings attached to a specific chat via `chat_instance`.

    The inline query has the form `search:<chat_instance>`.  The handler looks up
    all messages stored with that `chat_instance`, collects the unique active
    meetings, sorts them by relevance, and returns them as inline results.
    """
    lang = await user_language(update, session)
    query = valid_inline_query(update).query
    chat_instance = query.removeprefix(SEARCH_QUERY_PREFIX)

    # Chain `meetup -> messages` explicitly: from a Message root the selectin cascade reaches the
    # meetup but stops before `meetup.messages` (the Message mapper is revisited, closing a load-path
    # cycle), leaving that collection unloaded. The inline render below does not touch it, but
    # `Meetup.has_message`/`add_message` iterate it, so load it here to keep the meetings self-consistent.
    statement = (
        select(Message)
        .where(Message.chat_instance == chat_instance)
        .options(
            selectinload(cast("QueryableAttribute[Any]", Message.meetup)).selectinload(
                cast("QueryableAttribute[Any]", Meetup.messages)
            )
        )
    )
    messages = (await session.exec(statement)).all()

    # Collect unique active meetings via the already-loaded relationship
    seen: set[int] = set()
    meetings: list[Meetup] = []
    dropped_no_meetup = 0
    dropped_inactive = 0
    dropped_duplicate = 0
    for message in messages:
        meeting = message.meetup
        if not meeting:
            dropped_no_meetup += 1
        elif not meeting.active:
            dropped_inactive += 1
        elif meeting.db_id in seen:
            dropped_duplicate += 1
        else:
            seen.add(meeting.db_id)
            meetings.append(meeting)

    log.info(
        "Chat meeting search answered",
        chat_instance=chat_instance,
        messages_found=len(messages),
        meetings_listed=len(meetings),
        dropped_no_meetup=dropped_no_meetup,
        dropped_inactive=dropped_inactive,
        dropped_duplicate=dropped_duplicate,
        lang=lang,
        reason=None if meetings else "no_active_meetings_for_chat_instance",
    )

    if meetings:
        sorted_meetings = sort_meetings(meetings)
        results = [meeting_views.inline_view(meeting, chat_instance=chat_instance) for meeting in sorted_meetings]
    else:
        results = [
            MitupInlineView(
                description=InlineQueryMessages.NO_RESULTS_MESSAGE.get(lang=lang),
                keyboard=[[search_chat_meetings_button(lang=lang, chat_instance=chat_instance)]],
                id="no_meetings_found",
                title=InlineQueryMessages.NO_RESULTS_TITLE.get(lang=lang),
                inline_description=InlineQueryMessages.NO_RESULTS_DESCRIPTION.get(lang=lang),
            ),
        ]

    await context.api.answer_inline_query(update=update, results=results, cache_time=0)
    context.put_feature_metric(Feature.SEARCH_CHAT_MEETINGS)
