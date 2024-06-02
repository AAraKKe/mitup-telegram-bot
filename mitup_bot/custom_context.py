import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import auto
from typing import Any, override

from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.unit import Unit
from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot

from mitup_bot.exceptions import (
    ContextPropertyConversionError,
    ContextPropertyNotSetError,
    InvalidUserData,
)
from mitup_bot.monitoring import (
    CamelCaseStrEnum,
    Feature,
    MetricKey,
    create_metrics_from_update,
    properties_from_update,
)


class ContextId(CamelCaseStrEnum):
    EDIT_MEETING_TITLE = auto()
    EDIT_MEETING_DESCRIPTION = auto()
    EDIT_MEETING_LOCATION_NAME = auto()
    EDIT_MEETING_LOCATION_COORDINATES = auto()
    EDIT_MEETING_MAX_PARTICIPANTS = auto()
    EDIT_MEETING_KICK_OUT_PARTICIPANTS = auto()
    EDIT_MEETING_TIME = auto()


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


class MitupContext[TB: ExtBot, TM: MetricsLogger](CallbackContext[TB, MitupUserData, dict, dict]):
    """
    Custom context for the Mitup bot that includes a user data registry. Access to the registry
    is provided through context managers that ensure that the data is removed once out of scope.
    """

    def __init__(
        self,
        application: Application,
        update: Update,
        metrics: TM,
    ):
        self.metrics = metrics
        self.telegram_update = update
        self.handler_dimensions: dict[str, str] = {}

        chat_id = update.effective_chat.id if update and update.effective_chat else None
        user_id = update.effective_user.id if update and update.effective_user else None
        super().__init__(application=application, chat_id=chat_id, user_id=user_id)

    def __get_user_data_property[T: int | str | bool](
        self, context: ContextId, property: str, property_type: type[T], ensure_clean: bool
    ) -> Generator[T, None, None]:
        """Retrive the meeting id stored in given context and remove it once out of the context manager"""
        self.metrics.set_property("ContextId", context.value)

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
                self.put_metric("CleanUserData", 1, unit=Unit.COUNT)
            raise

        if ensure_clean:
            self.user_data.remove_context(context)
            self.put_metric("CleanUserData", 1, unit=Unit.COUNT)

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
        self.metrics.set_property("ContextId", context.value)
        self.put_metric("StoredMeetingId", 1, unit=Unit.COUNT)

    def clean_user_data(self, contexts: list[ContextId]):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean user data. Not doing anything.")
            return

        for context in contexts:
            self.user_data.remove_context(context)
            self.metrics.set_property("ContextId", context.value)
            self.put_metric("CleanUserData", 1, unit=Unit.COUNT)

    def clean_all_user_data(self):
        if self.user_data is None:  # pragma: no cover
            logging.warning("User data requested but not set when trying to clean all user data. Not doing anything.")
            return

        self.user_data.registry.clear()

    def put_metric(self, name: str | MetricKey, value: float, unit: Unit = Unit.COUNT):
        """
        This method puts a metric in the context for the request that is being processed. This is useful when
        we want to emit metrics with the default request dimensions, e.g. Handler and HandlerType. If no specific
        dimension is needed, this method should be used instead of `emit_metric`. This method does not flush the
        metrics, batching metrics with the same dimensions in the same log line.

        Args:
            name (str | MetricKey): Name of the metric to emit
            value (float): Value of the metric
            unit (Unit, optional): Dimension of the metric from `aws_embedded_metrics.unit.Unit`.
                Defaults to Unit.COUNT.
        """
        self.metrics.put_metric(str(name), value, unit.value)

    def prepare_handler_metrics(
        self,
        handler_dimensions: dict[str, str] | None = None,
    ):
        self.handler_dimensions = handler_dimensions or {}
        self.metrics.put_dimensions(self.handler_dimensions)

    async def emit_metric(
        self,
        name: str | MetricKey,
        value: float,
        unit: Unit = Unit.COUNT,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
        with_handler_dimensions: bool = False,
        with_update_properties: bool = False,
    ):
        """
        Emit a metric with the provided name, value, unit, dimensions and properties. This method will create a new
        metrics logger and flush it inmediately. This method should only be used when metrics are needed with a
        different set of dimensions when compared to the context dimensions.

        Args:
            name (str | MetricKey): Name of the metric to emit
            value (float): Value of the metric
            unit (Unit, optional): Dimension of the metric from `aws_embedded_metrics.unit.Unit`.
                Defaults to Unit.COUNT.
            dimensions (dict[str, str], optional): Dimensions to include in the metric. Defaults to None.
            properties (dict[str, Any], optional): Properties to include in the metric. Defaults to None.
            include_handler_dimensions (bool, optional): If True, the dimensions defined for the handler will be
            included.
        """
        metrics = self.metrics.new()

        dimensions = dimensions or {}
        if with_handler_dimensions:
            dimensions = dimensions | self.handler_dimensions
        metrics.set_dimensions(dimensions)

        properties = properties or {}
        if with_update_properties:
            properties = properties | properties_from_update(self.telegram_update)
        for k, v in properties.items():
            metrics.set_property(k, v)

        metrics.put_metric(str(name), value, unit.value)
        await self.flush_metrics(metrics)

    async def emit_feature_metric(
        self,
        feature: str | Feature,
        value: float = 1.0,
        name: str = MetricKey.COUNT,
        unit: Unit = Unit.COUNT,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, str | int | float | None] | None = None,
        with_handler_dimensions: bool = False,
        with_update_properties: bool = False,
    ):
        """
        A feature metric is a metric as any other that is emitted with a feature dimension. This is useful
        when we want to emit feature usage metrics, e.g. how many times timezone is set using the location
        or the message method.

        The arguments are the same as in `emit_metric` but with the addition of the `feature` argument that
        define the feature name.

        Since we are usually interested in the feature usage, i.e. how many times it is used, the metric name
        is set to Count by default. However, any metric name can be provided if needed.
        """
        dimensions = (dimensions or {}) | {"Feature": str(feature)}
        await self.emit_metric(
            name, value, unit, dimensions, properties, with_handler_dimensions, with_update_properties
        )

    async def flush_metrics(self, logger: MetricsLogger | None = None):
        # If we are requesting to flush a stand alone metrics logger, flush it
        if logger:
            await logger.flush()
        elif self.metrics.context.metrics:
            # Otherwise, if there are any metrics in the metrics context, flush them
            await self.metrics.flush()

        # Force instant flush of the metrics logger to avoid logs delays
        sys.stdout.flush()

    @classmethod
    @override
    def from_update(cls, update: object, application: Application) -> "MitupContext[TB, MetricsLogger]":
        assert isinstance(update, Update), "This should never happen, type is always Update in Mitupbot"

        logger = create_metrics_from_update(update)

        return MitupContext(application, update=update, metrics=logger)
