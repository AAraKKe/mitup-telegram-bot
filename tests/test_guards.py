import pytest
from sqlmodel import Session
from telegram import Chat, Message, Update

from mitup_bot.exceptions import EffectiveChatNotSet, EffectiveMessageNotSet, EffectiveUserNotSet, UserNotFound
from mitup_bot.guards import chat, current_user, message
from mitup_bot.models import User
from tests.helpers import UpdateRequest
from tests.stub_db import MockDbSession


@pytest.mark.parametrize("tg_update", [UpdateRequest(user=False)], indirect=True)
def test_current_user_fails_without_effective_user(mock_session: Session, tg_update: Update):
    with pytest.raises(EffectiveUserNotSet):
        current_user(tg_update, mock_session)


def test_current_user_fails_if_user_not_in_db(mock_session: MockDbSession, tg_update: Update):
    with pytest.raises(UserNotFound):
        current_user(tg_update, mock_session)


def test_current_user_succeeds(mock_session: MockDbSession, tg_update: Update, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert user_with_settings == current_user(tg_update, mock_session)


@pytest.mark.parametrize("tg_update", [UpdateRequest(chat=False)], indirect=True)
def test_chat_fails_without_effective_chat(tg_update: Update):
    with pytest.raises(EffectiveChatNotSet):
        chat(tg_update)


def test_chat_succeeds(tg_chat: Chat, tg_update: Update):
    assert tg_chat == chat(tg_update)


@pytest.mark.parametrize("tg_update", [UpdateRequest(message=False)], indirect=True)
def test_message_fails_without_effective_chat(tg_update: Update):
    with pytest.raises(EffectiveMessageNotSet):
        message(tg_update)


def test_message_succeeds(tg_message: Message, tg_update: Update):
    assert tg_message == message(tg_update)
