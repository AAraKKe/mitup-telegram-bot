import datetime as dt
from typing import cast

import pytest
from telegram import CallbackQuery, Chat, Message, Update
from telegram import User as TelegramUser

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.create_meeting import callback_query_create_meeting
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory as views_factory
from tests.helpers import MockDbSession, StubMitupApp, StubMitupContext, UpdateRequest, call_handler


async def enter_conversation(chat: Chat, user: TelegramUser, app: StubMitupApp) -> tuple[StubMitupContext, Update]:
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

    entry_context, _ = await call_handler(MeetingHandlerId.CREATE_MEETING_CONVERSATION, update=entry_update, app=app)

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
    context, _ = await call_handler(MeetingHandlerId.CREATE_MEETING_CONVERSATION, update=update, app=app)

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
    context, _ = await call_handler(MeetingHandlerId.CREATE_MEETING_CONVERSATION, update=update, app=app)

    # No meeting has been crated
    assert len(mock_session.objects_added) == 0

    # User sent to main menu
    context.api.assert_edit_message_called(update, views_factory.main_menu_view(lang=user_with_settings.lang), times=1)


async def test_callback_query_create_meeting_stores_on_exit(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_create_meeting(update, context)

    assert context.user_data is not None
    on_exit = context.user_data.registry[ContextId.CREATE_MEETING].on_exit
    assert on_exit is not None
    assert on_exit.message == MeetingMessages.CREATE_MEETING_ON_EXIT.get(lang=user_with_settings.lang)
    assert on_exit.cancel_callback == cb.CANCEL_CREATE_MEETING
