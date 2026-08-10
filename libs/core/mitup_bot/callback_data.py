import datetime as dt
import re
from enum import StrEnum
from typing import Self, override

import structlog
from pydantic import BaseModel, Field, field_validator

log = structlog.get_logger(__name__)

UNKNOWN_ENTITY = "unknown"


def regex_alternation(*forms: str) -> str:
    """Regex alternation over *forms*, dropping repeats and keeping first-seen order."""
    return "|".join(dict.fromkeys(forms))


class MeetingListSource(StrEnum):
    """Identifies which meeting list a detail view was opened from.

    Encoded in the callback data (see PaginatedCallbackData) so the detail view's back button
    can target the originating list. Values are single characters to stay well within
    Telegram's 64-byte callback data limit.
    """

    ACTIVE = "a"
    JOINED = "j"


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
    # Retired `(action, entity)` wire pairs this callback still accepts. Keyboards on already-sent
    # messages keep the wire string they were built with, so a renamed callback has to keep
    # answering the form sitting in Telegram's history. Only a declaration carries the table:
    # rendering and `parse` both produce the primary form, so derived callbacks have none.
    # Excluded from serialization: keyboards are persisted as message JSON, and that stored wire
    # format carries data only — a row must not embed a matching table that the declaration owns.
    aliases: tuple[tuple[str, str], ...] = Field(default=(), exclude=True)

    def __str__(self):
        return f"{self.action};{self.entity}:{'' if self.id is None else self.id}"

    def parse(self, match: re.Match | None) -> Self:
        if match is None:
            return self.model_validate({"entity": UNKNOWN_ENTITY})

        parsed = self.model_validate(match.groupdict())
        if (parsed.action, parsed.entity) != (self.action, self.entity):
            log.info(
                "Aliased callback wire form used",
                wire_form=f"{parsed.action};{parsed.entity}",
                primary_form=f"{self.action};{self.entity}",
            )
        return parsed

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: str | int):
        # If id is parsed as an empty string from the callback data, let it be None
        # Allow pydantic to handle conversion from str to int later.
        return value or None if isinstance(value, str) else value

    @property
    def pattern_body(self) -> str:
        """Unanchored regex for this callback's wire format.

        Subclasses extend the wire format by appending their own suffix to
        `super().pattern_body`; `pattern` anchors the composed body exactly once.
        """
        # Alternation is per part because `re` rejects a duplicate group name, which rules out
        # alternating whole bodies. The accepted language therefore also contains the crossbreeds of
        # a primary part with an alias part. Callback data is client-forgeable and is trusted no
        # further than the re-authorized id, and a crossbreed routes to the same handler as the real
        # forms, so accepting them costs nothing.
        actions = regex_alternation(self.action, *(action for action, _ in self.aliases))
        entities = regex_alternation(self.entity, *(entity for _, entity in self.aliases))
        return f"(?P<action>{actions});(?P<entity>{entities}):(?P<id>\\d*)"

    @property
    def pattern(self) -> str:
        return f"^{self.pattern_body}$"

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
    def pattern_body(self) -> str:
        return rf"{super().pattern_body};date:(?P<date>\d{{4}}-\d{{2}}-\d{{2}})"

    def with_date(self, date: dt.date) -> Self:
        return self.__class__(entity=self.entity, action=self.action, id=self.id, date=date)


class ValidCodeCallbackData(BaseModel):
    """Validated CodeCallbackData: the code is present and non-empty."""

    entity: str
    action: str
    code: str


class CodeCallbackData(CallbackData):
    """CallbackData addressed by an unguessable code instead of a record id.

    Record ids are small sequential integers, so a button carrying one can be forged into a button
    naming somebody else's row. Where a callback acts on a row the presser must already hold a
    secret for, the secret itself is the safer address: guessing it is the same problem as guessing
    the secret. `id` stays unset on the wire, and `is_malformed` gates on the code instead.

    The code alphabet is base64url, which is why the pattern accepts `[A-Za-z0-9_-]`.

    Format string: {action};{entity}:;code:{code}
    Example: "confirm;plink:;code:xLd2-mQ7..."
    """

    code: str | None = None

    def __str__(self) -> str:
        return f"{super().__str__()};code:{self.code or ''}"

    @property
    @override
    def pattern_body(self) -> str:
        return f"{super().pattern_body};code:(?P<code>[A-Za-z0-9_-]*)"

    @field_validator("code", mode="before")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        # An absent code is parsed as an empty string from the wire format; normalize it to None
        # so `is_malformed` has a single shape to check.
        return value or None

    @override
    def is_malformed(self) -> bool:
        return self.code is None or self.unknown()

    def with_code(self, code: str) -> Self:
        """Creates a CodeCallbackData with the same information but a different code."""
        return self.__class__(entity=self.entity, action=self.action, code=code)


class ValidPaginatedCallbackData(ValidCallbackData):
    """Validated PaginatedCallbackData with a concrete originating page.

    The wire format leaves the page optional, but a validated callback always carries a page:
    the guard defaults a missing page to the first page so handlers never re-derive it.
    The originating list stays optional: None means the detail was not reached from a list.
    """

    page: int
    source: MeetingListSource | None = None


class PaginatedCallbackData(CallbackData):
    """CallbackData that remembers the list page (and list) a detail view was opened from.

    A detail view reached from a paginated list must be able to return to the exact page
    the user navigated from instead of defaulting to page 1. The originating page is encoded
    after the id, optionally followed by which list the user came from so views shared by
    several lists (e.g. the meeting detail) can target the right one.

    Both suffixes are optional: a plain `with_id(...)` callback keeps the same wire format as
    a non-paginated `CallbackData` (no `;page:`/`;src:` suffix), so callbacks that have no
    originating page stay backward-compatible.

    Format string: {action};{entity}:{id}[;page:{page}][;src:{source}]
    Example: "show;meeting:42;page:3;src:j"
    """

    page: int | None = Field(default=None, ge=1)
    source: MeetingListSource | None = None

    def __str__(self) -> str:
        page_suffix = "" if self.page is None else f";page:{self.page}"
        source_suffix = "" if self.source is None else f";src:{self.source}"
        return f"{super().__str__()}{page_suffix}{source_suffix}"

    @property
    def pattern_body(self) -> str:
        # Both suffixes are optional so callbacks built with `with_id` remain wire-compatible
        # with a plain CallbackData.
        sources = "".join(source.value for source in MeetingListSource)
        return f"{super().pattern_body}(?:;page:(?P<page>\\d+))?(?:;src:(?P<source>[{sources}]))?"

    @field_validator("page", "source", mode="before")
    @classmethod
    def validate_optional_suffixes(cls, value: str | int | None) -> str | int | None:
        # A missing optional suffix group is parsed as None; let pydantic coerce the rest.
        return value or None if isinstance(value, str) else value

    def with_page(self, id: int, page: int, source: MeetingListSource | None = None) -> Self:
        """Attach the record id, the originating list page, and optionally which list it is."""
        return self.__class__(entity=self.entity, action=self.action, id=id, page=page, source=source)

    @override
    def with_id(self, id: int) -> Self:
        return self.__class__(entity=self.entity, action=self.action, id=id, page=self.page, source=self.source)


class ValidGrantCallbackData(ValidCallbackData):
    """Validated GrantCallbackData: the target user id and the tier rank are both present."""

    level: int


class GrantCallbackData(CallbackData):
    """CallbackData for the admin supporter-grant flow.

    The id addresses the target user's row and `level` carries the picked tier as its rank in the
    supporter policy's `LEVEL_ORDER`, so every button of the flow is self-contained and the
    conversation needs no per-user draft state. The rank is re-validated server-side on every tap.

    Format string: {action};{entity}:{id};lvl:{rank}
    Example: "set;grant:42;lvl:2"
    """

    level: int | None = Field(default=None, ge=0)

    def __str__(self) -> str:
        return f"{super().__str__()};lvl:{'' if self.level is None else self.level}"

    @property
    @override
    def pattern_body(self) -> str:
        return f"{super().pattern_body};lvl:(?P<level>\\d*)"

    @field_validator("level", mode="before")
    @classmethod
    def validate_level(cls, value: str | int | None) -> str | int | None:
        # A missing level is parsed as an empty string from the wire format; normalize it to None
        # so `is_malformed` has a single shape to check.
        return value or None if isinstance(value, str) else value

    @override
    def is_malformed(self) -> bool:
        return super().is_malformed() or self.level is None

    def with_level(self, id: int, level: int) -> Self:
        """Creates a GrantCallbackData addressing the target user `id` with the tier rank `level`."""
        return self.__class__(entity=self.entity, action=self.action, id=id, level=level)


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
    def pattern_body(self) -> str:
        return f"{super().pattern_body}:(?P<meeting_id>\\d*)"

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
