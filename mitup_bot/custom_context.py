import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto

from telegram.ext import CallbackContext, ExtBot

from mitup_bot.exceptions import ContextPropertyConversionError, ContextPropertyNotSetError, InvalidUserData


class ContextId(Enum):
    EDIT_MEETING_TITLE = auto()
    EDIT_MEETING_DESCRIPTION = auto()
    EDIT_MEETING_LOCATION_NAME = auto()
    EDIT_MEETING_LOCATION_COORDINATES = auto()
    EDIT_MEETING_MAX_PARTICIPANTS = auto()
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = auto()


@dataclass
class ContextData:
    meeting_id: int | None = None


@dataclass
class MitupUserData:
    registry: dict[ContextId, ContextData] = field(default_factory=dict)

    def remove_context(self, context: ContextId):
        self.registry.pop(context, None)

    def store_meeting_id(self, context: ContextId, meeting_id: int):
        self.registry.setdefault(context, ContextData()).meeting_id = meeting_id

    def has_meeting_id(self, context: ContextId) -> bool:
        return context in self.registry and self.registry[context].meeting_id is not None


class MitupContext[TB: ExtBot](CallbackContext[TB, MitupUserData, dict, dict]):
    """
    Custom context for the Mitup bot that includes a user data registry. Access to the registry
    is provided through context managers that ensure that the data is removed once out of scope.
    """

    def __get_user_data_property[T: int | str | bool](
        self, context: ContextId, property: str, type: type[T], ensure_clean: bool
    ) -> Generator[T, None, None]:
        """Retrive the meeting id stored in given context and remove it once out of the context manager"""
        if (
            self.user_data is None
        ):  # pragma: no cover, the app does not allow us to set user_data in tests and this should never happen
            raise InvalidUserData("User data requested but not set")

        entry = self.user_data.registry.get(context)

        if entry is None or (value := getattr(entry, property)) is None:
            raise ContextPropertyNotSetError(
                f"User data {property!r} requested but not set. User data: {self.user_data!r}"
            )

        try:
            value = type(value)
        except ValueError as exc:
            raise ContextPropertyConversionError(context.name, property, value) from exc

        try:
            yield value
        except Exception:
            if ensure_clean:
                self.user_data.remove_context(context)
            raise

        if ensure_clean:
            self.user_data.remove_context(context)

    @contextmanager
    def meeting_id(self, context: ContextId, ensure_clean=True) -> Generator[int, None, None]:
        """Retrive the meeting id stored in given context and remove it once out of the context manager"""
        yield from self.__get_user_data_property(context, "meeting_id", int, ensure_clean=ensure_clean)

    def has_meeting_id(self, context: ContextId) -> bool:
        if self.user_data is None:  # pragma: no cover
            return False
        return self.user_data.has_meeting_id(context)

    def store_meeting_id(self, context: ContextId, meeting_id: int):
        if self.user_data is None:  # pragma: no cover
            raise InvalidUserData("User data requested but not set")

        self.user_data.store_meeting_id(context, meeting_id)

    def clean_user_data(self, contexts: list[ContextId]):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean user data. Not doing anything.")
            return

        for context in contexts:
            self.user_data.remove_context(context)

    def clean_all_user_data(self):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean all user data. Not doing anything.")
            return

        self.user_data.registry.clear()
