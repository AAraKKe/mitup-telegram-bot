import datetime as dt
from typing import cast

import pytest
from telegram import CallbackQuery, Chat, Location, Message, Update
from telegram import User as TelegramUser

from mitup_bot.custom_context import MitupContext
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory as views_factory
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler


async def enter_conversation(chat: Chat, user: TelegramUser, app: StubMitupApp) -> tuple[MitupContext, Update]:
    # Start the conversation for meeting creation

    entry_callback_data_str = str(cb.CREATE_MEETING)

    minimal_message_for_callback = Message(
        message_id=12345,
        date=dt.datetime.now(),
        chat=chat,
        from_user=user,
        text="Original message with button",
    )

    entry_callback_query = CallbackQuery(
        id="test_callback_id_entry",
        from_user=user,
        chat_instance="somechat",
        data=entry_callback_data_str,
        message=minimal_message_for_callback,
    )
    entry_update = Update(update_id=1001, callback_query=entry_callback_query)

    entry_context, _ = await call_handler(entry_update, app, MeetingHandlerId.CREATE_MEETING_CONVERSATION)

    return entry_context, entry_update


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="My test meeting")],
    indirect=True,
)
async def test_meeting_creation_successful(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: StubMitupApp,
):
    user = update.effective_user
    chat = update.effective_chat

    assert user is not None
    assert chat is not None

    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    entry_context, entry_update = await enter_conversation(chat, user, app)

    # We now process the new update with the title of the meeting
    context, _ = await call_handler(update, app, MeetingHandlerId.CREATE_MEETING_CONVERSATION)

    # We should have created a new meeting
    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    assert new_meeting.title == "My test meeting"

    message = MeetingMessages.CREATED_SUCCESS.get(title=new_meeting.title, lang=user_with_settings.lang)
    view = new_meeting.edit_view.with_context(message)
    context.api.assert_send_message_called(update, view)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_CREATE_MEETING)],
    indirect=True,
)
async def test_meeting_creation_cancelled(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: StubMitupApp,
):
    user = update.effective_user
    chat = update.effective_chat

    assert user is not None
    assert chat is not None

    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    await enter_conversation(chat, user, app)

    # We now process the new update with the title of the meeting
    context, _ = await call_handler(update, app, MeetingHandlerId.CREATE_MEETING_CONVERSATION)

    # No meeting has been crated
    assert len(mock_session.objects_added) == 0

    # User sent to main menu
    context.api.assert_edit_message_called(update, views_factory.main_menu_view(lang=user_with_settings.lang), times=1)


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(command="/start"),
        UpdateRequest(location=Location(latitude=1.0, longitude=1.0)),
    ],
    indirect=True,
    ids=["command_message", "location_message"],
)
async def test_filter_messages_without_text_in_conversation(
    update: Update,
    app: StubMitupApp,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    """
    Test that after starting meeting creation, sending a non-text message (command or location)
    prompts the invalid title message and keeps the conversation in the TITLE state.
    """
    update_user = update.effective_user
    chat = update.effective_chat
    assert chat is not None
    assert update_user is not None

    mock_session.add_user(user_with_settings)

    # --- Step 1: Call the entry point to start the conversation ---
    # Construct the Update object for the entry point (callback query)
    entry_context, entry_update = await enter_conversation(chat, update_user, app)

    expected_entry_view = views_factory.create_meeting_view(lang=user_with_settings.lang)
    entry_context.api.assert_edit_message_called(update=entry_update, view=expected_entry_view)

    # --- Step 2: User sends an invalid message (command or location) ---
    invalid_msg_update = update

    expected_invalid_title_message = MeetingMessages.INVALID_TITLE.get(lang=user_with_settings.lang)

    invalid_title_context, _ = await call_handler(invalid_msg_update, app, MeetingHandlerId.CREATE_MEETING_CONVERSATION)

    expected_invalid_title_view = views_factory.create_meeting_view(
        lang=user_with_settings.lang, message=expected_invalid_title_message
    )
    invalid_title_context.api.assert_send_message_called(update=invalid_msg_update, view=expected_invalid_title_view)
