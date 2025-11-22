import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import auto
from time import perf_counter
from typing import override

from aws_embedded_metrics.unit import Unit
from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot

from mitup_bot.exceptions import (
    ContextPropertyConversionError,
    ContextPropertyNotSetError,
    InvalidUserData,
)
from mitup_bot.monitoring import (
    NULL_DIMENSIONALITY,
    CamelCaseStrEnum,
    Dimensionality,
    Feature,
    MetricKey,
    MitupMetricsEngine,
    MitupMetricsLogger,
    properties_from_update,
)


class ContextId(CamelCaseStrEnum):
    """Enum that identifies the different contexts that can be stored in the MitupUserData registry."""

    EDIT_MEETING_TITLE = auto()
    EDIT_MEETING_DESCRIPTION = auto()
    EDIT_MEETING_LOCATION_NAME = auto()
    EDIT_MEETING_LOCATION_COORDINATES = auto()
    EDIT_MEETING_MAX_PARTICIPANTS = auto()
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = auto()
    EDIT_MEETING_TIME = auto()


@dataclass
class ContextData:
    """Data class that represents the data to be stored per user in the MitupUserData registry."""

    meeting_id: int | None = None


@dataclass
class MitupUserData:
    """Class that represents the user data type of MitupContext"""

    registry: dict[ContextId, ContextData] = field(default_factory=dict)

    def remove_context(self, context: ContextId):
        self.registry.pop(context, None)

    def store_meeting_id(self, context: ContextId, meeting_id: int):
        self.registry.setdefault(context, ContextData()).meeting_id = meeting_id

    def has_meeting_id(self, context: ContextId) -> bool:
        return context in self.registry and self.registry[context].meeting_id is not None


class MitupContext[TB: ExtBot, TME: MitupMetricsEngine](CallbackContext[TB, MitupUserData, dict, dict]):
    """
    Custom context for the Mitup bot that includes several utilities.

    - User data registry. Access to the registry is provided through context managers that ensure that
    the data is removed once out of scope.
    - Metrics. The context includes a metrics engine that allows emitting metrics with context specific
    dimensions and properties as well as custom metrics with different dimensions.
    """

    def __init__(
        self,
        application: Application,
        update: Update,
        # Python does not yet support generic of generics, until then we can keep this as TME
        metrics_engine: TME,
    ):
        self.metrics_engine = metrics_engine
        self.telegram_update = update
        self.handler_dimensionality = NULL_DIMENSIONALITY
        self.__update = update

        chat_id = update.effective_chat.id if update and update.effective_chat else None
        user_id = update.effective_user.id if update and update.effective_user else None

        super().__init__(application=application, chat_id=chat_id, user_id=user_id)

    def get_update(self) -> Update:
        return self.__update

    @property
    def handler_metrics_logger(self) -> MitupMetricsLogger:
        return self.metrics_engine.get_logger(self.handler_dimensionality)

    def __get_user_data_property[T: int | str | bool](
        self, context: ContextId, property: str, property_type: type[T], ensure_clean: bool
    ) -> Generator[T]:
        """
        Retrive a given property stored in the user data.

        If ensure_clean is True, the property is removed after yielding it
        """
        self.handler_metrics_logger.set_property("ContextId", context.value)

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
            raise ContextPropertyConversionError(context.name, property, value) from exc

        try:
            yield value
        except Exception:
            if ensure_clean:
                self.user_data.remove_context(context)
                self.emit_metric("CleanUserData", 1, unit=Unit.COUNT)
            raise

        if ensure_clean:
            self.user_data.remove_context(context)
            self.emit_metric("CleanUserData", 1, unit=Unit.COUNT)

    @contextmanager
    def meeting_id(self, context: ContextId, ensure_clean=True) -> Generator[int]:
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
        self.handler_metrics_logger.set_property("StoredMeetingId", meeting_id)
        self.handler_metrics_logger.set_property("ContextId", context.value)
        self.emit_metric("StoredMeetingId", 1, unit=Unit.COUNT)

    def clean_user_data(self, contexts: list[ContextId]):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean user data. Not doing anything.")
            return

        for context in contexts:
            self.user_data.remove_context(context)
            self.metrics_engine.properties["ContextId"] = context.value
            self.emit_metric("CleanUserData", 1, unit=Unit.COUNT)

    def clean_all_user_data(self):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean all user data. Not doing anything.")
            return

        self.user_data.registry.clear()

    def prepare_handler_metrics(
        self,
        handler_dimensions: dict[str, str] | None = None,
    ):
        if not handler_dimensions:
            return

        self.handler_dimensionality = Dimensionality(**handler_dimensions)

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: Unit = Unit.COUNT,
        *,
        # Dimension control
        dimensions: dict[str, str] | None = None,
        include_handler_dimensions: bool = True,
        # Property control
        properties: dict[str, str | int | float | None] | None = None,
        include_update_properties: bool = True,
        # Special flag for global aggregation
        emit_global: bool = False,
    ):
        """
        Emit a metric with flexible dimension and property configuration.

        Metrics are batched by dimensionality and flushed at the end of handler execution.
        Multiple metrics with the same dimensions are combined into a single EMF log line for cost optimization.

        Args:
            name (str | MetricKey): Metric name (use MetricKey enum for standard metrics)
            value (float): Metric value
            unit (Unit): Unit type (COUNT, MILLISECONDS, etc.)
            dimensions (dict[str, str], optional): Additional custom dimensions to add. Defaults to None.
            include_handler_dimensions (bool): If True, includes Handler and HandlerType dimensions. Defaults to True.
            properties (dict[str, str | int | float | None], optional): Additional custom properties. Defaults to None.
            include_update_properties (bool): If True, includes Telegram update metadata. Defaults to True.
            emit_global (bool): If True, also emits the metric without any dimensions for global aggregation.
                Defaults to False.

        Examples:
            # Simple handler metric (most common case)
            context.emit_metric(MetricKey.ERROR, 1)

            # Custom dimensions without handler context
            context.emit_metric("ApiCall", 1, dimensions={"Service": "Google"}, include_handler_dimensions=False)

            # Emit both with handler dims AND global (for aggregation)
            context.emit_metric(MetricKey.FAULT, 0, emit_global=True)

            # Feature metric
            context.emit_metric(MetricKey.COUNT, dimensions={"Feature": "JoinMeeting"})
        """
        # Build dimensionality
        dimensionality = Dimensionality.or_null(dimensions)
        if include_handler_dimensions:
            dimensionality += self.handler_dimensionality

        # Build properties
        props = properties or {}
        if include_update_properties:
            props |= properties_from_update(self.telegram_update)

        # Emit primary metric
        logger = self.metrics_engine.get_logger(dimensionality, props)
        logger.put_metric(str(name), value, unit.value)

        # Optionally emit global version
        if emit_global:
            global_props = properties or {}
            if include_update_properties:
                global_props |= properties_from_update(self.telegram_update)
            global_logger = self.metrics_engine.get_logger(NULL_DIMENSIONALITY, global_props)
            global_logger.put_metric(str(name), value, unit.value)

    def put_feature_metric(
        self,
        feature: Feature,
        value: float = 1.0,
        name: str | MetricKey = MetricKey.COUNT,
        unit: Unit = Unit.COUNT,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, str | int | float | None] | None = None,
        with_handler_dimensions: bool = False,
        with_update_properties: bool = True,
    ):
        """
        Emit a metric with a Feature dimension for tracking feature usage.

        This is a convenience wrapper around emit_metric() that automatically adds the Feature dimension.
        Useful for tracking feature usage like "how many times timezone is set using location vs message".

        Args:
            feature (Feature): The feature being tracked
            value (float): Metric value. Defaults to 1.0.
            name (str | MetricKey): Metric name. Defaults to COUNT for counting feature usage.
            unit (Unit): Unit type. Defaults to COUNT.
            dimensions (dict[str, str], optional): Additional dimensions. Defaults to None.
            properties (dict[str, str | int | float | None], optional): Additional properties. Defaults to None.
            with_handler_dimensions (bool): Include handler dimensions. Defaults to False.
            with_update_properties (bool): Include update properties. Defaults to True.

        Examples:
            # Track feature usage
            context.put_feature_metric(Feature.JOIN_MEETING)

            # Track feature errors
            context.put_feature_metric(Feature.TIMEZONE_WITH_MESSAGE, name=MetricKey.ERROR, value=1)
        """
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
        """
        Context manager that emits a Time metric measuring the time it takes the code inside the context to run.

        Args:
            prefix (str): The prefix added to the metric name. The resulting metric name will be <prefix>Time.
            handler_metrics (bool): If True, includes handler dimensions in the metric. Defaults to False.

        Examples:
            # Measure API call time without handler dimensions
            with context.with_time_metric("TelegramApi"):
                await context.bot.send_message(...)

            # Measure operation time with handler dimensions
            with context.with_time_metric("ProcessPayment", handler_metrics=True):
                process_payment()
        """
        start_time = perf_counter()
        yield
        elapsed_time = 1000 * (perf_counter() - start_time)

        self.emit_metric(
            MetricKey.TIME.with_prefix(prefix, separator=""),
            elapsed_time,
            Unit.MILLISECONDS,
            include_handler_dimensions=handler_metrics,
        )

    async def flush_metrics(self):
        # If we are requesting to flush a stand alone metrics logger, flush it
        await self.metrics_engine.flush_metrics()

    @classmethod
    @override
    def from_update(
        cls, update: object, application: Application, metrics_engine: MitupMetricsEngine | None = None
    ) -> MitupContext[TB, MitupMetricsEngine]:
        assert isinstance(update, Update), "This should never happen, type is always Update in Mitupbot"

        metrics_engine = metrics_engine or MitupMetricsEngine(logger_provider=lambda ep: MitupMetricsLogger(ep))
        return MitupContext(application, update=update, metrics_engine=metrics_engine)
