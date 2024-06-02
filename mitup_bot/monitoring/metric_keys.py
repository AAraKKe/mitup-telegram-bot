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

    def with_prefix(self, prefix: str, separator: str = "/") -> str:
        return f"{prefix}{separator}{self.value}"


class Feature(CamelCaseStrEnum):
    TIMEZONE_WITH_MESSAGE = auto()
    TIMEZONE_WITH_LOCATION = auto()
    NEW_USER_REGISTERED = auto()
    NEW_LANDING = auto()
    CREATE_MEETING = auto()
