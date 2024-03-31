import pytest
from sqlmodel import Session
from telegram import Chat, Message, Update

from mitup_bot.exceptions import EffectiveChatNotSet, EffectiveMessageNotSet, EffectiveUserNotSet, UserNotFound
from mitup_bot.guards import chat, current_user, message
from mitup_bot.models import User
from tests.helpers import UpdateRequest
from tests.stub_db import MockDbSession


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
