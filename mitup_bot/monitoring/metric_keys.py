# This module contais static metric names to be sure to use always the same names no matter
# where we emit the metric from
from enum import StrEnum, auto


class CamelCaseStrEnum(StrEnum):
    """This is a string enum that has, as value, the camel case version of the enum name."""

    @property
    def value(self) -> str:
        # Instead of having the snakecase lets use CamelCase
        return self.name.title().replace("_", "")

    def __str__(self) -> str:
        return self.value


class MetricKey(CamelCaseStrEnum):
    TIME = auto()
    COUNT = auto()
    ERROR = auto()
    """This is a metric to be emitted when there is a processing error, user input error, etc."""
    FAULT = auto()
    """This is a metric to be emitted when there is a system fault, something that is not expected to happen."""
    MEETING_NOT_OWNED = auto()
    """Metric to be emitted when the user tries to do an action with a meeting that does not belong to them."""
    MESSAGE_DELETED = auto()
    """Represents a message that has been deleted in Telegram and deleted from the database"""
    STALE_MEETING_MESSAGE = auto()
    """This metrics is emitted when someone interacts with a message of a meeting that should not be available."""
    INACTIVE_USER_SET = auto()
    """Metric emitted when an inactive user has been detected and is_active is set to False."""
    INACTIVE_USERS_DELETED = auto()
    """Show how many notifications should be sent when a meeting is about to start"""
    NOTIFICATIONS_TO_SEND = auto()
    """Number of notifications sent when a meeting is about to start"""
    MEETING_NOTIFICATIONS_SENT = auto()

    def with_prefix(self, prefix: str, separator: str = "/") -> str:
        return f"{prefix}{separator}{self.value}"


class Feature(CamelCaseStrEnum):
    TIMEZONE_WITH_MESSAGE = auto()
    TIMEZONE_WITH_LOCATION = auto()
    NEW_USER_REGISTERED = auto()
    NEW_LANDING = auto()
    CREATE_MEETING = auto()
    SHARE_MEETING = auto()
    JOIN_MEETING = auto()
    LEAVE_MEETING = auto()
