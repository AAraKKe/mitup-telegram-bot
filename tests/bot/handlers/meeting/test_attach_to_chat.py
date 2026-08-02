import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.meeting.attach_to_chat import is_already_attached
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey, MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import MeetingAttachMessages, MeetingDisplayMessages
from mitup_bot.views import MitupView
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_message,
    create_user,
)
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.types import ClaimSharedCard

# The meeting an unregistered caller reaches, distinct from the ids the shared fixtures use.
SHARED_MEETING_ID = 456


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_new_message(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When the message is not yet tracked, a new Message is created with chat_instance."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    assert len(meeting.messages) == 0

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # A new message should have been created and attached
    assert len(meeting.messages) == 1
    assert meeting.messages[0].chat_instance is not None
    mock_session.assert_flushed()

    # The alert should be the "now searchable" one
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ENABLED_ALERT.get(),
        show_alert=True,
    )

    # Feature metric emitted
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.messages[0],
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_existing_message_without_chat_instance(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When a tracked message already exists but has no chat_instance, it gets updated."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Pre-existing message without chat_instance (inline_message_id matches the update default)
    existing_message = create_message(meetup_id=meeting.db_id)
    meeting.messages.append(existing_message)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # The existing message should now have a chat_instance
    assert existing_message.chat_instance is not None
    mock_session.assert_flushed()

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ENABLED_ALERT.get(),
        show_alert=True,
    )

    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_already_attached_in_other_chat(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When the meeting is attached in a different chat, a new message is created with the current chat_instance."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Pre-existing message attached to a different chat
    existing_message = create_message(
        inline_message_id="previous_inline_message",
        chat_instance="other_chat",
        meetup_id=meeting.db_id,
    )
    meeting.messages.append(existing_message)

    assert len(meeting.messages) == 1

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # A new message should have been created for the current chat
    assert len(meeting.messages) == 2
    assert meeting.messages[1].chat_instance == "someinstance"

    # The pre-existing message keeps its original chat_instance
    assert existing_message.chat_instance == "other_chat"

    # The alert should be "now searchable" (not "already searchable")
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ENABLED_ALERT.get(),
        show_alert=True,
    )

    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_already_attached_in_same_chat(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When the meeting is already attached to this chat via another message, show 'already searchable' alert."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Pre-existing message from a previous share in the same chat (same chat_instance)
    existing_message = create_message(
        inline_message_id="previous_inline_message",
        chat_instance="someinstance",
        meetup_id=meeting.db_id,
    )
    meeting.messages.append(existing_message)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # The "already searchable" alert should be shown
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ALREADY_ENABLED_ALERT.get(),
        show_alert=True,
    )

    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(999), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_meeting_not_found(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When the meeting no longer exists, inform the user and emit stale metric."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # The user has been notified that the meeting was deleted. The tapped card is an inline message,
    # which can sit in any chat, so the banner that replaces it carries no navigation.
    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MitupView(
            description=MeetingDisplayMessages.DELETED_BANNER.get(lang=user_with_settings.lang), keyboard=[]
        ),
    )

    metrics.assert_emitted(name=MetricKey.FAULT, value=0.0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)

    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_existing_message_with_chat_instance_unchanged(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When a tracked message already has chat_instance set, it is not overwritten (elif branch skipped)."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Pre-existing message WITH chat_instance already set (inline_message_id matches the update default)
    existing_message = create_message(meetup_id=meeting.db_id, chat_instance="existing_instance")
    meeting.messages.append(existing_message)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # chat_instance must remain the same (not overwritten)
    assert existing_message.chat_instance == "existing_instance"
    mock_session.assert_flushed()

    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


# --- Callers without an account ------------------------------------------------------------------


def shared_meeting_of_a_stranger(mock_session: MockDbSession, title: str) -> Meetup:
    """A meeting owned by somebody the acting Telegram user has no relationship with."""
    meeting = create_meetup(SHARED_MEETING_ID, title, owner=create_user(id=999, tg_user_id=999, first_name="Owner"))
    mock_session.add_object(meeting)
    return meeting


def marked_for_deletion(mock_session: MockDbSession) -> User:
    marked_user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, first_name="Test", status=UserStatus.DELETION_REQUESTED)
    mock_session.add_user(marked_user)
    return marked_user


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(SHARED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_by_unregistered_user(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    claim_shared_card: ClaimSharedCard,
):
    """Making a meeting findable is a property of the chat, so any reader of the card may do it.

    The button rides on every card that is not already searchable, and whoever received it need
    never have started the bot.
    """
    meeting = shared_meeting_of_a_stranger(mock_session, "Shared Meeting")
    shared_card = claim_shared_card(meeting)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert meeting.messages == [shared_card]
    assert shared_card.chat_instance == "someinstance"
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ENABLED_ALERT.get(),
        show_alert=True,
    )
    context.api.assert_update_meeting_messages_called(meeting=meeting, current_message=shared_card)
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})
    # Attaching without an account is a normal interaction: nothing faults, and a discovery tap is
    # too low an intent to create a profile from.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    mock_session.assert_not_added()


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(SHARED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_by_unregistered_user_on_unclaimed_card_is_rejected(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """An inline message no meeting claims authorizes nothing, and there is no account to fall back on.

    Anyone can manufacture such a card by sharing a meeting of their own, so honouring it would
    publish a stranger's private meeting into the caller's chat.
    """
    meeting = shared_meeting_of_a_stranger(mock_session, "Private Meeting")

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert meeting.messages == []
    context.api.assert_update_meeting_messages_not_called()
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingDisplayMessages.DELETED_BANNER.get(lang=TranslationEngine.FALLBACK_LANG),
        show_alert=True,
    )
    metrics.assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    mock_session.assert_not_added()


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(SHARED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_by_pending_deletion_user(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    claim_shared_card: ClaimSharedCard,
):
    """A user marked for deletion counts as unregistered: the card authorizes the tap on its own."""
    marked_for_deletion(mock_session)
    meeting = shared_meeting_of_a_stranger(mock_session, "Shared Meeting")
    shared_card = claim_shared_card(meeting)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert shared_card.chat_instance == "someinstance"
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingAttachMessages.ENABLED_ALERT.get(),
        show_alert=True,
    )
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})
    # The dying account is never consulted, so no pending-deletion rejection reaches the caller.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(SHARED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_attach_to_chat_by_pending_deletion_owner_is_rejected(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Ownership stops counting once the account is marked for deletion, exactly as for a stranger."""
    marked_user = marked_for_deletion(mock_session)
    meeting = create_meetup(SHARED_MEETING_ID, "Own Meeting", owner=marked_user)
    mock_session.add_object(meeting)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert meeting.messages == []
    context.api.assert_update_meeting_messages_not_called()
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingDisplayMessages.DELETED_BANNER.get(lang=TranslationEngine.FALLBACK_LANG),
        show_alert=True,
    )
    metrics.assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


def test_is_already_attached_returns_false_when_chat_instance_is_none():
    """is_already_attached returns False immediately when chat_instance is None, regardless of meeting messages."""
    meeting = create_meetup(id=1, title="Test")

    result = is_already_attached(meeting, chat_instance=None)

    assert result is False  # early return branch at line 19
