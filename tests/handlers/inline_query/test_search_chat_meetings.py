import datetime as dt
from collections.abc import Callable

import pytest
from sqlmodel import select
from telegram import Update
from telegram.ext import Application

from mitup_bot.handlers.inline_query.enums import SEARCH_QUERY_PREFIX, InlineQueryId
from mitup_bot.models import Meetup, Message, User
from mitup_bot.monitoring import Feature
from mitup_bot.utils.messages import InlineViewMessages
from mitup_bot.views import MitupInlineView
from tests.helpers import (
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_message,
    create_user,
)

CHAT_INSTANCE = "test_chat_instance"


def _register_messages_query(mock_session: MockDbSession, messages: list[Message]):
    statement = select(Message).where(Message.chat_instance == CHAT_INSTANCE)
    mock_session.add_objects_with_statement(statement, tuple(messages))


def _make_message(*, id: int, meetup_id: int) -> Message:
    return create_message(id=id, chat_instance=CHAT_INSTANCE, meetup_id=meetup_id)


def _no_meetings_found_view(lang: str) -> MitupInlineView:
    return MitupInlineView(
        description=InlineViewMessages.NO_MEETINGS_FOUND_MESSAGE.get(lang=lang),
        keyboard=[],
        id="no_meetings_found",
        title=InlineViewMessages.NO_MEETINGS_FOUND_TITLE.get(lang=lang),
        inline_description=InlineViewMessages.NO_MEETINGS_FOUND_DESCRIPTION.get(lang=lang),
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=f"{SEARCH_QUERY_PREFIX}{CHAT_INSTANCE}")], indirect=True)
async def test_search_returns_matching_meetings(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    mock_session.add_user(user_with_settings)

    owner = user_with_settings
    meeting = create_meetup(10, "Team Standup", owner=owner, active=True)

    msg = _make_message(id=1, meetup_id=meeting.db_id)
    msg.meetup = meeting

    _register_messages_query(mock_session, [msg])

    context, _ = await call_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[meeting.inline_view(chat_instance=CHAT_INSTANCE)],
        cache_time=0,
    )
    context.metrics_engine.assert_feature_metrics_emitted(Feature.SEARCH_CHAT_MEETINGS)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=f"{SEARCH_QUERY_PREFIX}{CHAT_INSTANCE}")], indirect=True)
async def test_search_returns_no_meetings_found_when_empty(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    mock_session.add_user(user_with_settings)
    _register_messages_query(mock_session, [])

    context, _ = await call_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[_no_meetings_found_view(user_with_settings.lang)],
        cache_time=0,
    )


def _inactive_scenario(owner: User) -> tuple[list[Message], Meetup]:
    """Two meetings shared, but one is inactive — only the active one should be returned."""
    active = create_meetup(10, "Active", owner=owner, active=True)
    inactive = create_meetup(11, "Inactive", owner=owner, active=False)
    msg1 = _make_message(id=1, meetup_id=active.db_id)
    msg1.meetup = active
    msg2 = _make_message(id=2, meetup_id=inactive.db_id)
    msg2.meetup = inactive
    return [msg1, msg2], active


def _duplicate_scenario(owner: User) -> tuple[list[Message], Meetup]:
    """Same meeting shared twice — should appear only once in results."""
    meeting = create_meetup(10, "Shared Twice", owner=owner, active=True)
    msg1 = _make_message(id=1, meetup_id=meeting.db_id)
    msg1.meetup = meeting
    msg2 = _make_message(id=2, meetup_id=meeting.db_id)
    msg2.meetup = meeting
    return [msg1, msg2], meeting


@pytest.mark.parametrize(
    "update, build_scenario",
    [
        (UpdateRequest(inline_query=f"{SEARCH_QUERY_PREFIX}{CHAT_INSTANCE}"), _inactive_scenario),
        (UpdateRequest(inline_query=f"{SEARCH_QUERY_PREFIX}{CHAT_INSTANCE}"), _duplicate_scenario),
    ],
    indirect=["update"],
    ids=["excludes_inactive", "deduplicates"],
)
async def test_search_filters_to_single_result(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    build_scenario: Callable[[User], tuple[list[Message], Meetup]],
):
    mock_session.add_user(user_with_settings)
    messages, expected_meeting = build_scenario(user_with_settings)
    _register_messages_query(mock_session, messages)

    context, _ = await call_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[expected_meeting.inline_view(chat_instance=CHAT_INSTANCE)],
        cache_time=0,
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=f"{SEARCH_QUERY_PREFIX}{CHAT_INSTANCE}")], indirect=True)
async def test_search_sorts_by_relevance(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
):
    """Future meetings first, then no datetime, then past."""
    now = dt.datetime.now(tz=dt.UTC)

    owner = create_user(id=1, tg_user_id=123, first_name="Owner")
    mock_session.add_user(owner)

    past_meeting = create_meetup(1, "Past", owner=owner, datetime=now - dt.timedelta(days=7))
    future_meeting = create_meetup(2, "Future", owner=owner, datetime=now + dt.timedelta(days=7))
    no_date_meeting = create_meetup(3, "No Date", owner=owner)
    no_date_meeting.created_time = now

    msg1 = _make_message(id=1, meetup_id=past_meeting.db_id)
    msg1.meetup = past_meeting
    msg2 = _make_message(id=2, meetup_id=future_meeting.db_id)
    msg2.meetup = future_meeting
    msg3 = _make_message(id=3, meetup_id=no_date_meeting.db_id)
    msg3.meetup = no_date_meeting

    _register_messages_query(mock_session, [msg1, msg2, msg3])

    context, _ = await call_handler(InlineQueryId.SEARCH_CHAT_MEETINGS, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[
            future_meeting.inline_view(chat_instance=CHAT_INSTANCE),
            no_date_meeting.inline_view(chat_instance=CHAT_INSTANCE),
            past_meeting.inline_view(chat_instance=CHAT_INSTANCE),
        ],
        cache_time=0,
    )
