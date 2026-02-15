import pytest
from aws_embedded_metrics.unit import Unit

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import AnyFloat, HandlerContext, MockDbSession, UpdateRequest, call_handler, create_message


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_new_message(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
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
        text=MeetingMessages.NOW_SEARCHABLE_ALERT.get(plain=True),
        show_alert=True,
    )

    # Feature metric emitted
    context.metrics_engine.assert_feature_metrics_emitted(Feature.ATTACH_TO_CHAT)

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
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
        text=MeetingMessages.NOW_SEARCHABLE_ALERT.get(plain=True),
        show_alert=True,
    )

    context.metrics_engine.assert_feature_metrics_emitted(Feature.ATTACH_TO_CHAT)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_already_attached_in_other_chat(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
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
        text=MeetingMessages.NOW_SEARCHABLE_ALERT.get(plain=True),
        show_alert=True,
    )

    context.metrics_engine.assert_feature_metrics_emitted(Feature.ATTACH_TO_CHAT)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(1), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_already_attached_in_same_chat(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
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
        text=MeetingMessages.ALREADY_SEARCHABLE_ALERT.get(plain=True),
        show_alert=True,
    )

    context.metrics_engine.assert_feature_metrics_emitted(Feature.ATTACH_TO_CHAT)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(999), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_meeting_not_found(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """When the meeting no longer exists, inform the user and emit stale metric."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    # The user has been notified that the meeting was deleted
    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user_with_settings.lang),
    )

    # Stale meeting metric emitted
    context.metrics_engine.assert_metrics_emited(
        [MetricKey.STALE_MEETING_MESSAGE, MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
        [1.0, 0.0, AnyFloat(), 0],
        [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=False,
    )

    context.metrics_engine.assert_feature_metrics_not_emitted(Feature.ATTACH_TO_CHAT)
