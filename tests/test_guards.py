import logging
from contextlib import nullcontext

import pytest
from _pytest.python_api import RaisesContext
from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import Chat, Message, Update

from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    UserNotFound,
)
from mitup_bot.guards import callback_query, chat, current_user, meeting_accessible, message
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard, MitupView
from tests.helpers import MockApi, StubMitupContext, UpdateRequest, create_meetup
from tests.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.guards") as api:
        yield api


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_current_user_fails_without_effective_user(mock_session: Session, update: Update):
    with pytest.raises(EffectiveUserNotSet):
        current_user(update, mock_session)


def test_current_user_fails_if_user_not_in_db(mock_session: MockDbSession, update: Update):
    with pytest.raises(UserNotFound):
        current_user(update, mock_session)


def test_current_user_succeeds(mock_session: MockDbSession, update: Update, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert user_with_settings == current_user(update, mock_session)


@pytest.mark.parametrize("update", [UpdateRequest(chat=False)], indirect=True)
def test_chat_fails_without_effective_chat(update: Update):
    with pytest.raises(EffectiveChatNotSet):
        chat(update)


def test_chat_succeeds(tg_chat: Chat, update: Update):
    assert tg_chat == chat(update)


@pytest.mark.parametrize("update", [UpdateRequest(message=False)], indirect=True)
def test_message_fails_without_effective_chat(update: Update):
    with pytest.raises(EffectiveMessageNotSet):
        message(update)


def test_message_succeeds(tg_message: Message, update: Update):
    assert tg_message == message(update)


@pytest.mark.parametrize(
    "update, expect",
    [
        (UpdateRequest(callback_query=False), pytest.raises(CallbackQueryNotSet)),
        (UpdateRequest(callback_query=True), nullcontext()),
    ],
    indirect=["update"],
    ids=["callback_query_not_set", "callback_query_set"],
)
def test_callback_query(update: Update, expect: RaisesContext):
    with expect:
        callback_query(update)


async def test_meeting_accesible_works_with_a_meeting_that_belong_to_an_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    with caplog.at_level(logging.WARNING):
        result = await meeting_accessible(mock_session, user_with_settings, 1, "Test method", update, context)

        assert user_with_settings.meetups[0] == result
        assert caplog.text == ""

    api.assert_edit_message_not_called()
    api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "keyboard",
    [
        None,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(), callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1)
                ),
            ]
        ],
    ],
    ids=["without_custom_keyboard", "with_custom_keyboard"],
)
async def test_meeting_accessible_fails_with_meeting_that_does_not_exist(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    keyboard: Keyboard | None,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        result = await meeting_accessible(
            mock_session, user_with_settings, 999, "Test method", update, context, custom_keyboard=keyboard
        )
        # Call flush metrics that usually would be called by the handler
        await context.flush_metrics()

        assert "User tried 'Test method' with a meeting that does not exist." in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text
        assert result is None

        api.assert_edit_message_called(
            context,
            update,
            MitupView(
                description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(),
                keyboard=keyboard or [[ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU)]],
            ),
        )


async def test_meeting_accessible_fails_with_meeting_that_does_not_belong_to_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    meeting = create_meetup(999, "Meeting!", description="Description")
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with caplog.at_level(logging.WARNING):
        result = await meeting_accessible(mock_session, user_with_settings, 999, "Test method", update, context)
        # Call flush metrics that usually would be called by the handler
        await context.flush_metrics()

        assert "User tried 'Test method' with a meeting that does not belong to them. " in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text
        assert result is None

        api.assert_edit_message_called(context, update, factory.main_menu_view())
        context.metrics.assert_metrics_emited([MetricKey.ERROR.with_prefix("MeetingNotOwned")], [1], [Unit.COUNT])
