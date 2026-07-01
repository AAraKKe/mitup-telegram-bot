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
    """
    Represents the callback data used in Telegram inline keyboards. It encodes an action,
    an entity, and an optional ID.

    The action represents what is to be done, the entity represents on what it is to be done,
    and the ID represents the specific subject over which the action is to be performed.

    Format string: {action};{entity}:{id}
    Example: "edit;meet_title:42"
    """

    entity: str
    action: str = "show"
    id: int | None = Field(default=None, ge=0)

    def __str__(self):
        return f"{self.action};{self.entity}:{'' if self.id is None else self.id}"

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
    This callback data extends the basic CallbackData with a date field.

    Format string: {action};{entity}:{id};date:{YYYY-MM-DD}
    Example: "set_date;meeting:0;date:2023-10-15"
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


class ValidPaginatedCallbackData(ValidCallbackData):
    """Validated PaginatedCallbackData with a concrete originating page.

    The wire format leaves the page optional, but a validated callback always carries a page:
    the guard defaults a missing page to the first page so handlers never re-derive it.
    """

    page: int


class PaginatedCallbackData(CallbackData):
    """CallbackData that remembers the list page a detail view was opened from.

    A detail view reached from a paginated list must be able to return to the exact page
    the user navigated from instead of defaulting to page 1. The originating page is encoded
    after the id.

    The page is optional: a plain `with_id(...)` callback keeps the same wire format as a
    non-paginated `CallbackData` (no `;page:` suffix), so callbacks that have no originating
    page stay backward-compatible.

    Format string: {action};{entity}:{id}[;page:{page}]
    Example: "show;past_meeting:42;page:3"
    """

    page: int | None = Field(default=None, ge=1)

    def __str__(self) -> str:
        return super().__str__() if self.page is None else f"{super().__str__()};page:{self.page}"

    @property
    def pattern(self) -> str:
        # The page suffix is optional so callbacks built with `with_id` remain wire-compatible
        # with a plain CallbackData.
        return f"{super().pattern[:-1]}(?:;page:(?P<page>\\d+))?$"

    @field_validator("page", mode="before")
    @classmethod
    def validate_page(cls, value: str | int | None) -> str | int | None:
        # A missing optional page group is parsed as None; let pydantic coerce the rest.
        return value or None if isinstance(value, str) else value

    def with_page(self, id: int, page: int) -> Self:
        """Attach both the record id and the originating list page."""
        return self.__class__(entity=self.entity, action=self.action, id=id, page=page)

    @override
    def with_id(self, id: int) -> Self:
        return self.__class__(entity=self.entity, action=self.action, id=id, page=self.page)


class ValidMeetingCallbackData(ValidCallbackData):
    """
    Callback data to be used when performing an action on a meeting
    The `id` field represents the id associated with the action that is to be performed in the meeting
    represented by the `meeting_id` field.
    """

    meeting_id: int


class MeetingCallbackData(CallbackData):
    """
    This is the same as a CallbackData but with an additional meeting_id field.

    This CallbackData implementation can be used when the subject of the action is nto the meeting
    itself but the action is tied to a sepecific meeting. For example, kick out a user from a meeting.

    The id in the callback is the subject, i.e. the user to kick out, but the meeting_id is provided
    as well as needed context.

    Format string: {action};{entity}:{id}:{meeting_id}
    Example: "kickout;user:15:42" (kick out user with id 15 from meeting with id 42)
    """

    meeting_id: int | None = None

    def __str__(self):
        return f"{self.action};{self.entity}:{'' if self.id is None else self.id}:{self.meeting_id or ''}"

    @property
    def pattern(self) -> str:
        return f"^(?P<action>{self.action});(?P<entity>{self.entity}):(?P<id>\\d*):(?P<meeting_id>\\d*)$"

    @field_validator("meeting_id", mode="before")
    @classmethod
    def validate_ids(cls, value: str | int):
        # If meeting_id is parsed as an empty string from the callback data, let it be None
        # Allow pydantic to handle conversion from str to int later.
        return value or None if isinstance(value, str) else value

    def with_ids(self, meeting_id: int, id: int) -> Self:
        """Creates a MeetingCallbackData with the same information but different IDs"""
        return self.__class__(entity=self.entity, action=self.action, id=id, meeting_id=meeting_id)

    @override
    def with_id(self, id: int) -> Self:
        return self.__class__(entity=self.entity, action=self.action, id=id, meeting_id=self.meeting_id)
