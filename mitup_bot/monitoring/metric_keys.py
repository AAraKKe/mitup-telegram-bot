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
    """Metric to be emitted when the time is measured"""

    TIME = auto()
    """Metric to be emitted when the count is measured"""
    COUNT = auto()
    """This is a metric to be emitted when there is a processing error, user input error, etc."""
    ERROR = auto()
    """This is a metric to be emitted when there is a system fault, something that is not expected to happen."""
    FAULT = auto()
    """Metric to be emitted when the user tries to do an action with a meeting that does not belong to them."""
    MEETING_NOT_OWNED = auto()
    """Represents a message that has been deleted in Telegram and deleted from the database"""
    MESSAGE_DELETED = auto()
    """This metrics is emitted when someone interacts with a message of a meeting that should not be available."""
    STALE_MEETING_MESSAGE = auto()
    """Metric emitted when an inactive user has been detected and is_active is set to False."""
    INACTIVE_USER_SET = auto()
    """Metric emitted by the user cleanup lambda with the number of inactive users found and to be deleted"""
    INACTIVE_USERS_DELETED = auto()
    """Number of notifications failed to send"""
    NOTIFICATIONS_FAILED = auto()
    """Number of notifications sent when a meeting is about to start"""
    NOTIFICATIONS_SENT = auto()
    """Show how many notifications should be sent when a meeting is about to start"""
    NOTIFICATIONS_TO_SEND = auto()
    """Stat for total number of active users"""
    ACTIVE_USERS = auto()
    """Stat for the number of inactive users"""
    INACTIVE_USERS = auto()
    """Stat for the total of invited users"""
    INVITED_USERS = auto()
    """Stat for the total number of active meetings"""
    ACTIVE_MEETINGS = auto()
    """Stat for the total number of meetings with a datetime set"""
    MEETINGS_WITH_DATETIME = auto()
    """Stat for the total number of inactive meetings"""
    INACTIVE_MEETINGS = auto()
    """Stat for the total number of incognito meetings"""
    INCOGNITO_MEETINGS = auto()
    """Stat for the total number of public meetings"""
    PUBLIC_MEETINGS = auto()
    """Stat for the total number of meetings with invitation"""
    MEETINGS_WITH_INVITATION = auto()
    """Stat for the total number of shared meetings"""
    SHARED_MEETINGS = auto()
    """Number of meetings that should be deactivated"""
    MEETINGS_TO_DEACTIVATE = auto()
    """Number of meetings successfully deactivated"""
    MEETINGS_DEACTIVATED = auto()
    """Number of meetings that failed to be deactivated"""
    MEETINGS_DEACTIVATION_FAILED = auto()
    """Number of meetings that should be deleted"""
    MEETUPS_ABOUT_TO_BE_DELETED = auto()
    """Number of meetings successfully deleted"""
    MEETUPS_DELETED = auto()

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
    KICK_OUT_PARTICIPANT = auto()
    MEETING_LANGUAGE_SET = auto()
