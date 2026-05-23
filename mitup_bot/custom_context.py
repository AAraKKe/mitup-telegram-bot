import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import auto
from time import perf_counter
from typing import Any, Generic, TypeVar

from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot

from mitup_bot.api_wrapper import TelegramApi, TelegramApiWrapper
from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import (
    ContextPropertyConversionError,
    ContextPropertyNotSetError,
    InvalidUserData,
)
from mitup_bot.monitoring import (
    CamelCaseStrEnum,
    Feature,
    MetricKey,
    properties_from_update,
)
from mitup_bot.monitoring.backend import EmfBackend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.utils.entities import FormattedText


class ContextId(CamelCaseStrEnum):
    """Enum that identifies the different contexts that can be stored in the MitupUserData registry."""

    # Edit Meeting Contexts
    EDIT_MEETING_TITLE = auto()
    EDIT_MEETING_DESCRIPTION = auto()
    EDIT_MEETING_LOCATION_NAME = auto()
    EDIT_MEETING_LOCATION_COORDINATES = auto()
    EDIT_MEETING_MAX_PARTICIPANTS = auto()
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = auto()
    EDIT_MEETING_TIME = auto()
    EDIT_MEETING_DURATION = auto()
    EDIT_MEETING_END_DATETIME = auto()

    # Create Meeting
    CREATE_MEETING = auto()

    # Edit Settings
    EDIT_SETTINGS_TIMEZONE = auto()

    # Invite users
    INVITE_USERS = auto()


@dataclass
class OnExit:
    """Data class that holds the information to show when a conversation is unexpectedly interrupted."""

    message: FormattedText
    cancel_callback: CallbackData


@dataclass
class ContextData:
    """Data class that represents the data to be stored per user in the MitupUserData registry."""

    meeting_id: int | None = None
    text: FormattedText | None = None
    on_exit: OnExit | None = None


@dataclass
class MitupUserData:
    """Class that represents the user data type of MitupContext"""

    registry: dict[ContextId, ContextData] = field(default_factory=dict)
    active_context: ContextId | None = None

    def remove_context(self, context: ContextId):
        self.registry.pop(context, None)
        if self.active_context == context:
            self.active_context = None

    def store_meeting_id(self, context: ContextId, meeting_id: int):
        self.registry.setdefault(context, ContextData()).meeting_id = meeting_id

    def store_text(self, context: ContextId, text: str | FormattedText):
        ftext = text if isinstance(text, FormattedText) else FormattedText(text)
        self.registry.setdefault(context, ContextData()).text = ftext

    def has_meeting_id(self, context: ContextId) -> bool:
        return context in self.registry and self.registry[context].meeting_id is not None

    def store_on_exit(self, context: ContextId, message: str | FormattedText, cancel_callback: CallbackData) -> None:
        fmessage = message if isinstance(message, FormattedText) else FormattedText(message)
        entry = self.registry.setdefault(context, ContextData())
        entry.on_exit = OnExit(message=fmessage, cancel_callback=cancel_callback)
        self.active_context = context

    def get_active_on_exit(self) -> OnExit | None:
        """Return the on-exit data for the most recently entered conversation, if any."""
        if self.active_context is None:
            return None
        entry = self.registry.get(self.active_context)
        return entry.on_exit if entry is not None else None


# Old TypeVar syntax kept (rather than PEP 695) so we can name TB/TAPI as importable
# module-level symbols. The class itself is invariant in both parameters: covariance over
# TB is unsound (CallbackContext is invariant in its bot type because Application.bot is a
# writable attribute), and covariance over TAPI would be unsound for the same reason
# (self.api is a writable attribute). Substitutability across prod and test parameterizations
# is achieved by making downstream signatures (TMitupContext) parametric in TB/TAPI rather
# than by lying about variance here.
TB = TypeVar("TB", bound=ExtBot)
TAPI = TypeVar("TAPI", bound=TelegramApiWrapper)


class MitupContext(
    CallbackContext[TB, MitupUserData, dict, dict],
    Generic[TB, TAPI],  # noqa: UP046
):
    """
    Custom context for the Mitup bot that includes several utilities.

    - User data registry. Access to the registry is provided through context managers that ensure that
    the data is removed once out of scope.
    - Metrics. The context includes a metrics client that allows emitting metrics with context specific
    dimensions and properties as well as custom metrics with different dimensions.
    """

    def __init__(
        self,
        application: Application,
        update: Update,
        metrics: MetricsClient,
        api: TAPI,
    ):
        self.metrics = metrics
        self.telegram_update = update
        self._handler_dimensions: dict[str, str] = {}
        self.__update = update

        chat_id = update.effective_chat.id if update and update.effective_chat else None
        user_id = update.effective_user.id if update and update.effective_user else None

        super().__init__(application=application, chat_id=chat_id, user_id=user_id)

        self.api = api
        api.adapter = self

    def get_update(self) -> Update:
        return self.__update

    def __get_user_data_property[T: int | str | bool](
        self, context: ContextId, property: str, property_type: type[T], ensure_clean: bool
    ) -> Generator[T]:
        """
        Retrive a given property stored in the user data.

        If ensure_clean is True, the property is removed after yielding it
        """
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
            value = property_type(value)
        except ValueError as exc:
            raise ContextPropertyConversionError(context.name, property, value, property_type) from exc

        try:
            yield value
        except Exception:
            if ensure_clean:
                self.user_data.remove_context(context)
                self.emit_metric("CleanUserData", properties={"ContextId": context.value})
            raise

        if ensure_clean:
            self.user_data.remove_context(context)
            self.emit_metric("CleanUserData", properties={"ContextId": context.value})

    @contextmanager
    def meeting_id(self, context: ContextId, ensure_clean=True) -> Generator[int]:
        """Retrive the meeting id stored in given context and remove it once out of the context manager"""
        yield from self.__get_user_data_property(context, "meeting_id", int, ensure_clean=ensure_clean)

    @contextmanager
    def text(self, context: ContextId, ensure_clean=True) -> Generator[str]:
        """Retrive the text stored in given context and remove it once out of the context manager"""
        yield from self.__get_user_data_property(context, "text", str, ensure_clean=ensure_clean)

    def has_meeting_id(self, context: ContextId) -> bool:
        if self.user_data is None:  # pragma: no cover
            return False
        return self.user_data.has_meeting_id(context)

    def store_meeting_id(self, context: ContextId, meeting_id: int):
        if self.user_data is None:  # pragma: no cover
            raise InvalidUserData("User data requested but not set")

        self.user_data.store_meeting_id(context, meeting_id)
        self.emit_metric(
            "StoredMeetingId",
            properties={"StoredMeetingId": meeting_id, "ContextId": context.value},
        )

    def store_text(self, context: ContextId, text: str | FormattedText):
        if self.user_data is None:  # pragma: no cover
            raise InvalidUserData("User data requested but not set")

        ftext = text if isinstance(text, FormattedText) else FormattedText(text)
        self.user_data.store_text(context, ftext)
        self.emit_metric(
            "StoredContextText",
            properties={"ContextId": context.value, "StoredText": ftext.text},
        )

    def store_on_exit(self, context: ContextId, message: str | FormattedText, cancel_callback: CallbackData) -> None:
        fmessage = message if isinstance(message, FormattedText) else FormattedText(message)
        if self.user_data is None:  # pragma: no cover
            raise InvalidUserData("User data requested but not set")
        self.user_data.store_on_exit(context, fmessage, cancel_callback)

    def get_active_on_exit(self) -> OnExit | None:
        """Return the on-exit data for the most recently entered conversation, if any."""
        return None if self.user_data is None else self.user_data.get_active_on_exit()

    def clean_user_data(self, contexts: list[ContextId]):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean user data. Not doing anything.")
            return

        for context in contexts:
            self.user_data.remove_context(context)
            self.emit_metric("CleanUserData", properties={"ContextId": context.value})

    def clean_all_user_data(self):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean all user data. Not doing anything.")
            return

        self.user_data.registry.clear()
        self.user_data.active_context = None

    def prepare_handler_metrics(
        self,
        handler_dimensions: dict[str, str] | None = None,
    ):
        if not handler_dimensions:
            return

        self._handler_dimensions = handler_dimensions

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: MetricUnit = MetricUnit.COUNT,
        *,
        # Dimension control
        dimensions: dict[str, str] | None = None,
        include_handler_dimensions: bool = True,
        # Property control
        properties: dict[str, Any] | None = None,
        include_update_properties: bool = True,
        # Special flag for global aggregation
        emit_global: bool = False,
    ):
        """Emit a metric with flexible dimension and property configuration.

        Metrics with identical dimensions are batched into a single EMF log line — a CloudWatch
        cost optimization since charges are per log line, not per metric within a line.
        """
        dims = dict(dimensions or {})
        if include_handler_dimensions:
            dims |= self._handler_dimensions

        props = dict(properties or {})
        if include_update_properties:
            props |= properties_from_update(self.telegram_update)

        self.metrics.emit(name, value, unit, dimensions=dims, properties=props)

        if emit_global:
            global_dims = dict(dimensions or {})
            global_props = dict(properties or {})
            if include_update_properties:
                global_props |= properties_from_update(self.telegram_update)
            self.metrics.emit(name, value, unit, dimensions=global_dims, properties=global_props)

    def put_feature_metric(
        self,
        feature: Feature,
        value: float = 1.0,
        name: str | MetricKey = MetricKey.COUNT,
        unit: MetricUnit = MetricUnit.COUNT,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
        with_handler_dimensions: bool = False,
        with_update_properties: bool = True,
    ):
        """Convenience wrapper around emit_metric that adds a Feature dimension automatically."""
        feature_dimensions = (dimensions or {}) | {"Feature": str(feature)}
        self.emit_metric(
            name,
            value,
            unit,
            dimensions=feature_dimensions,
            properties=properties,
            include_handler_dimensions=with_handler_dimensions,
            include_update_properties=with_update_properties,
        )

    @contextmanager
    def with_time_metric(self, prefix: str, handler_metrics: bool = False) -> Generator[None]:
        """Measure elapsed time and emit a `<prefix>Time` metric in milliseconds on exit."""
        start_time = perf_counter()
        yield
        elapsed_time = 1000 * (perf_counter() - start_time)

        self.emit_metric(
            MetricKey.TIME.with_prefix(prefix, separator=""),
            elapsed_time,
            MetricUnit.MILLISECONDS,
            include_handler_dimensions=handler_metrics,
        )

    async def flush_metrics(self):
        await self.metrics.flush()

    @classmethod
    def from_update(
        cls,
        update: object,
        application: Application,
    ) -> MitupContext:
        assert isinstance(update, Update), "This should never happen, type is always Update in Mitupbot"

        metrics = MetricsClient(EmfBackend())

        return MitupContext(application, update=update, metrics=metrics, api=TelegramApi())
