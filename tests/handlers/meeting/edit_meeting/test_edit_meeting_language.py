import pytest
from telegram import Update

from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.models import Message, User
from mitup_bot.monitoring import Feature
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views import factory
from tests.helpers import StubMitupApp, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_LANGUAGE.with_id(10))],
    indirect=["update"],
    ids=["edit_meeting_language"],
)
async def test_callback_edit_meeting_language(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    """Test that the edit meeting language callback displays the language selection view."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", language="en")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.LANGUAGE_CALLBACK, update=update, app=app)

    context.api.assert_edit_message_called(
        update,
        factory.meeting_set_language_view(meeting=meeting),
    )


@pytest.mark.parametrize(
    "update,new_language_idx",
    [
        (UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE.with_ids(10, 0)), 0),  # English
        (UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE.with_ids(10, 1)), 1),  # Spanish
    ],
    indirect=["update"],
    ids=["set_language_to_english", "set_language_to_spanish"],
)
async def test_callback_set_meeting_language(
    mock_session: MockDbSession,
    update: Update,
    new_language_idx: int,
    user_with_settings: User,
    app: StubMitupApp,
):
    """Test that setting a meeting language updates the meeting and its messages."""
    # Start with a meeting in English
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", language="en")
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Add some messages to verify they get updated
    message1 = Message(message_id=111, chat_id=111, meetup=meeting)
    message2 = Message(message_id=222, chat_id=222, meetup=meeting)
    meeting.messages.extend([message1, message2])

    context, _ = await call_handler(EditMeetingHandlerId.SET_LANGUAGE_CALLBACK, update=update, app=app)

    # Verify the language was updated
    expected_language = SUPPORTED_LANGUAGES[new_language_idx]
    assert meeting.language == expected_language

    # Verify the keyboard for all messages was updated
    for message in meeting.messages:
        assert message.buttons.keyboard == meeting.build_inline_keyboard()

    # Verify flush was called
    mock_session.assert_flushed()

    # Verify the success message was shown
    context.api.assert_edit_message_called(
        update,
        factory.meeting_set_language_view(meeting=meeting).with_context(
            MeetingMessages.LANGUAGE_SET_SUCCESS.get(lang=meeting.user_language)
        ),
    )

    # Verify meeting messages were updated
    context.api.assert_update_meeting_messages_called(mock_session, meeting)

    # Verify feature metric was emitted
    context.metrics_engine.assert_feature_metrics_emitted(Feature.MEETING_LANGUAGE_SET)


@pytest.mark.parametrize(
    "update,initial_language",
    [
        (UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE.with_ids(10, 1)), "en"),  # en -> es_ES
        (UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE.with_ids(10, 0)), "es_ES"),  # es_ES -> en
    ],
    indirect=["update"],
    ids=["change_from_english_to_spanish", "change_from_spanish_to_english"],
)
async def test_callback_set_meeting_language_changes_all_message_keyboards(
    mock_session: MockDbSession,
    update: Update,
    initial_language: str,
    user_with_settings: User,
    app: StubMitupApp,
):
    """Test that changing language updates all message keyboards with the new language."""
    meeting = create_meetup(id=10, title="TestMeeting", description="Description", language=initial_language)
    user_with_settings.meetups.append(meeting)
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Add multiple messages with different chat IDs
    messages = [
        Message(message_id=111, chat_id=111, meetup=meeting),
        Message(message_id=222, chat_id=222, meetup=meeting),
        Message(message_id=333, chat_id=333, meetup=meeting),
    ]
    meeting.messages.extend(messages)

    context, _ = await call_handler(EditMeetingHandlerId.SET_LANGUAGE_CALLBACK, update=update, app=app)

    # Verify all message keyboards were updated
    for message in messages:
        new_keyboard = message.buttons.keyboard
        # The keyboards should be updated to the new language
        assert new_keyboard == meeting.build_inline_keyboard()

    # Verify meeting messages were updated via API
    context.api.assert_update_meeting_messages_called(mock_session, meeting)
