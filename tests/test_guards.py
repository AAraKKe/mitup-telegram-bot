import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

import pytest
from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import Chat, Message, Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    MalformedCallbackData,
    UserNotFound,
)
from mitup_bot.guards import (
    callback_query,
    chat,
    current_user,
    meeting_accessible,
    message,
    user_registered,
    valid_callback_data,
)
from mitup_bot.handlers.main_menu import MainMenuHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard, MitupView
from tests.helpers import StubMitupContext, UpdateRequest, create_meetup, create_user
from tests.helpers.stub_db import MockDbSession


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
def test_callback_query(update: Update, expect: AbstractContextManager):
    with expect:
        callback_query(update)


async def test_meeting_accesible_works_with_a_meeting_that_belong_to_an_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    with caplog.at_level(logging.WARNING):
        result = await meeting_accessible(mock_session, user_with_settings, 1, "Test method", update, context)

        assert user_with_settings.meetups[0] == result
        assert caplog.text == ""

    context.api.assert_edit_message_not_called()
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "keyboard",
    [
        lambda lang: None,
        lambda lang: [
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(lang=lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
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
    keyboard: Callable[[str], Keyboard | None],
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        result = await meeting_accessible(
            mock_session,
            user_with_settings,
            999,
            "Test method",
            update,
            context,
            custom_keyboard=keyboard(user_with_settings.lang),
        )
        # Call flush metrics that usually would be called by the handler
        await context.flush_metrics()

        assert "User tried 'Test method' with a meeting that does not exist." in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text
        assert result is None

        context.api.assert_edit_message_called(
            update,
            MitupView(
                description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(lang=user_with_settings.lang),
                keyboard=keyboard(user_with_settings.lang)
                or [
                    [
                        ButtonConfig(
                            text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                            callback_data=cb.MAIN_MENU,
                        )
                    ]
                ],
            ),
        )


async def test_meeting_accessible_fails_with_meeting_that_does_not_belong_to_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
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

        context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))
        context.metrics_engine.assert_metrics_emited(
            [MetricKey.ERROR.with_prefix("MeetingNotOwned")], [1], [Unit.COUNT]
        )


@pytest.mark.parametrize(
    "match", [re.match(cb.SHOW_MEETING.pattern, "show;meeting:"), None], ids=["no_id", "none_match"]
)
def test_valid_callback_data_failed_states(match: re.Match | None):
    with pytest.raises(MalformedCallbackData):
        valid_callback_data(CallbackData.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)


@pytest.mark.parametrize(
    "match",
    [
        re.match(cb.EDIT_MEETING_DATE.pattern, "edit;meet_date:;date:2024-12-02"),
        re.match(cb.EDIT_MEETING_DATE.pattern, "edit;meet_date:12;date:"),
        None,
    ],
    ids=["no_id", "no_date", "none_match"],
)
def test_valid_date_callback_data_failed_states(match: re.Match | None):
    with pytest.raises(MalformedCallbackData):
        valid_callback_data(CallbackData.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)


@pytest.mark.parametrize(
    "update, user_id, expectation",
    [
        [UpdateRequest(callback_query=CallbackData(entity="m", action="show", id=1)), 123, nullcontext()],
        [UpdateRequest(callback_query=CallbackData(entity="m", action="show", id=1)), 456, nullcontext()],
        [UpdateRequest(callback_query=False), 456, pytest.raises(CallbackQueryNotSet)],
    ],
    ids=["user_registered", "user_not_registered", "no_callback"],
    indirect=["update"],
)
async def test_context_manager_for_registered_user(
    mock_session: MockDbSession,
    update: Update,
    user_id: int,
    expectation: AbstractContextManager,
    context: StubMitupContext,
):
    # The update is created with a user with tg_user_id=123
    mock_session.add_user(create_user(1, tg_user_id=user_id))
    is_registered = user_id == 123

    with expectation:
        user = await user_registered(update, mock_session, context, MeetingMessages.INVITE_USER_OPEN_CHAT)
        if is_registered:
            assert user is not None
            assert user.tg_user_id == user_id
        else:
            context.api.assert_answer_callback_query_called(
                update,
                MeetingMessages.INVITE_USER_OPEN_CHAT.get(lang="en"),
                show_alert=True,
            )
