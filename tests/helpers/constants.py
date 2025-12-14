from dataclasses import dataclass

from mitup_bot.models import Message

DEFAULT_CURRENT_MESSAGE = Message(id=99999, inline_message_id="default_current_message")
"""Used as default value for update_meeting_messages_mock assertions because it is possible that the message is None"""


@dataclass
class DefaultValue[T]:
    # Helps differentiate between arguments that have been provided
    # and not provided to the mocked calls
    value: T


DEFAULT_FALSE = DefaultValue(False)
DEFAULT_NONE = DefaultValue(None)
