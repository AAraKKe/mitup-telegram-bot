"""Tracking of a meeting card at the moment it is shared.

Telegram reports the chosen inline result as soon as the card is sent, which is the only moment the
bot learns which meeting an inline message shows. Interactions with the card are authorized against
that binding, so the tests here pin that it is recorded once, for the right meeting, and only for
results that produced an inline message.
"""

import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup, Message
from mitup_bot.views import meeting as meeting_views
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler, create_message
from tests.helpers.constants import DEFAULT_INLINE_MESSAGE_ID
from tests.helpers.fixtures import create_update


def tracked_messages(mock_session: MockDbSession) -> list[Message]:
    return [obj for obj in mock_session.objects_added if isinstance(obj, Message)]


@pytest.mark.parametrize("update", [UpdateRequest(chosen_inline_result="123")], indirect=True)
async def test_shared_card_is_tracked_for_the_meeting(
    meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """Sharing a card binds its inline message to the meeting it shows."""
    mock_session.add_object(meeting)

    await call_handler(InlineQueryId.SHARED_MEETING, handler_context=handler_context)

    assert len(tracked_messages(mock_session)) == 1
    tracked = tracked_messages(mock_session)[0]
    assert tracked.inline_message_id == DEFAULT_INLINE_MESSAGE_ID
    assert tracked.meetup_id == meeting.db_id
    # No interaction has happened yet, so the chat the card landed in is still unknown.
    assert tracked.chat_instance is None
    assert tracked.chat_id is None
    assert tracked.message_id is None


@pytest.mark.parametrize("update", [UpdateRequest(chosen_inline_result="123")], indirect=True)
async def test_shared_card_stores_the_buttons_of_the_sent_card(
    meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """The tracked message carries the keyboard the shared card was sent with."""
    mock_session.add_object(meeting)

    await call_handler(InlineQueryId.SHARED_MEETING, handler_context=handler_context)

    keyboard = tracked_messages(mock_session)[0].buttons.keyboard
    callback_data = [button.callback_data for row in keyboard for button in row]
    assert cb.JOIN.with_id(meeting.db_id) in callback_data
    assert cb.ATTACH_TO_CHAT.with_id(meeting.db_id) in callback_data


@pytest.mark.parametrize("update", [UpdateRequest(chosen_inline_result="123", inline_message_id=None)], indirect=True)
async def test_result_without_an_inline_message_is_not_tracked(
    meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """Telegram allocates no inline message id for a result without buttons, so there is no card."""
    mock_session.add_object(meeting)

    await call_handler(InlineQueryId.SHARED_MEETING, handler_context=handler_context)

    assert tracked_messages(mock_session) == []


@pytest.mark.parametrize("update", [UpdateRequest(chosen_inline_result="123")], indirect=True)
async def test_already_tracked_card_is_not_tracked_twice(
    meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """Telegram redelivers an update whose webhook call timed out; the second delivery adds nothing."""
    mock_session.add_object(meeting)
    mock_session.add_object(
        create_message(inline_message_id=DEFAULT_INLINE_MESSAGE_ID, meetup_id=meeting.db_id), "inline_message_id"
    )

    await call_handler(InlineQueryId.SHARED_MEETING, handler_context=handler_context)

    assert tracked_messages(mock_session) == []


@pytest.mark.parametrize("update", [UpdateRequest(chosen_inline_result="999")], indirect=True)
async def test_share_of_a_deleted_meeting_is_not_tracked(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """The meeting can be deleted between answering the inline query and the card being sent."""
    await call_handler(InlineQueryId.SHARED_MEETING, handler_context=handler_context)

    assert tracked_messages(mock_session) == []


def test_non_meeting_results_are_not_handled():
    """The bot answers inline queries with more than meeting cards; only those carry a meeting id."""
    handler = HandlersRegistry.get_handler(InlineQueryId.SHARED_MEETING)
    update = create_update(UpdateRequest(chosen_inline_result="meetings_in_this_chat"))

    assert handler.check_update(update) is None


def test_shared_meeting_view_is_answered_with_the_meeting_id(meeting: Meetup):
    """The result id the bot answers with is what this handler reads the meeting id from."""
    assert meeting_views.inline_view(meeting).id == str(meeting.db_id)
