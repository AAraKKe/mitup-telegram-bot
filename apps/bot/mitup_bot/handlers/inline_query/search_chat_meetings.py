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

    statement = select(Message).where(Message.chat_instance == chat_instance)
    messages = (await session.exec(statement)).all()

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
