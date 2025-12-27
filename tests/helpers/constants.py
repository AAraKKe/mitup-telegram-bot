import datetime as dt
from dataclasses import dataclass

from mitup_bot.models import Message

DEFAULT_CURRENT_MESSAGE = Message(id=99999, inline_message_id="default_current_message")
"""Used as default value for update_meeting_messages_mock assertions because it is possible that the message is None"""


DEFAULT_TEST_DATE = dt.datetime(2023, 1, 1, 12, 00, tzinfo=dt.UTC)
DEFAULT_USER_ID = 123
DEFAULT_CHAT_ID = 123
DEFAULT_MESSAGE_ID = 123
DEFAULT_TG_USER_PARAMS = {
    "id": DEFAULT_USER_ID,
    "first_name": "Test User",
    "is_bot": False,
    "username": "testuser",
}


@dataclass
class DefaultValue[T]:
    # Helps differentiate between arguments that have been provided
    # and not provided to the mocked calls
    value: T


DEFAULT_FALSE = DefaultValue(False)
DEFAULT_NONE = DefaultValue(None)
