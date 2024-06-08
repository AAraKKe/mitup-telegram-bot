import datetime as dt
import re
from typing import Self, override

from pydantic import BaseModel, Field, field_validator

UNKNOWN_ENTITY = "unknown"


class ValidCallbackData(BaseModel):
    """This represents the same data as CallbackData but is not allowed to have id being None"""

    entity: str
    action: str
    id: int


class CallbackData(BaseModel):
    entity: str
    action: str = "show"
    id: int | None = Field(default=None, ge=0)

    def __str__(self):
        return f"{self.action};{self.entity}:{self.id or ''}"

    @classmethod
    def parse[T: BaseModel](cls: type[T], match: re.Match | None) -> T:
        if match is None:
            return cls.model_validate({"entity": UNKNOWN_ENTITY})
        return cls.model_validate(match.groupdict())

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: str | int):
        # If id is parsed as an empty string from the callback data, let it be None
        # Allow pydantic to handle conversion from str to int later.
        return value or None if isinstance(value, str) else value

    @property
    def pattern(self) -> str:
        return f"^(?P<action>{self.action});(?P<entity>{self.entity}):(?P<id>\\d*)$"

    def with_id(self, id: int) -> Self:
        """Creates a CallbackData with the same information but different ID"""
        return self.__class__(entity=self.entity, action=self.action, id=id)

    def unknown(self) -> bool:
        return self.entity == UNKNOWN_ENTITY

    def is_malformed(self) -> bool:
        """This means that the callback data provided in the query is not usable as needed by the callback"""
        return self.id is None or self.unknown()

    def match(self) -> re.Match:
        re_match = re.match(self.pattern, str(self))
        assert re_match is not None, f"CallbackData.match should always match the pattern: {self.pattern!r}"
        return re_match

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CallbackData):
            return False
        return self.id == other.id and self.entity == other.entity and self.action == other.action


class ValidDateCallbackData(ValidCallbackData):
    date: dt.date


class DateCallbackData(CallbackData):
    """
    A CallbackData that appends ';date:YYYY-MM-DD' to the end of the callback data
    and handles the pattern accordingly
    """

    date: dt.date | None = None

    def __str__(self) -> str:
        return f"{super().__str__()};date:{self.date:%Y-%m-%d}"

    @property
    def pattern(self) -> str:
        own_pattern = r";date:(?P<date>\d{4}-\d{2}-\d{2})$"
        return f"{super().pattern[:-1]}{own_pattern}"

    def with_date(self, date: dt.date) -> Self:
        return self.__class__(entity=self.entity, action=self.action, id=self.id, date=date)
