import sys
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from aws_embedded_metrics.config import get_config
from aws_embedded_metrics.environment import Environment
from aws_embedded_metrics.environment.environment_detector import EnvironmentCache
from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.serializers.log_serializer import LogSerializer
from aws_embedded_metrics.sinks import Sink
from aws_embedded_metrics.unit import Unit
from rich.console import Console

from mitup_bot.config import MetricsConfig, MetricsEnv
from mitup_bot.monitoring.record import MetricRecord
from mitup_bot.monitoring.units import MetricUnit


class MitupMetricsLogger(MetricsLogger):
    """Custom MetricsLogger for Mitup Bot that allows any custom behaviour we need to introduce"""

    def __init__(self, resolve_environment: Callable[..., Awaitable[Environment]]):
        context = MetricsContext.empty()
        context.should_use_default_dimensions = False
        super().__init__(resolve_environment, context)

    async def flush(self):
        await super().flush()
        # Force stdout flush to ensure the logs are emitted
        sys.stdout.flush()


class Dimensionality:
    def __init__(self, **dimensions: str):
        self.dimensions = {str(k): str(dimensions[k]) for k in sorted(dimensions)}
        self.hash = hash(tuple((k, v) for k, v in self.dimensions.items()))

    @staticmethod
    def or_null(dimensions: dict[str, str] | None) -> Dimensionality:
        return Dimensionality() if dimensions is None else Dimensionality(**dimensions)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, value: object) -> bool:
        return self.hash == value.hash if isinstance(value, Dimensionality) else False

    def __add__(self, other: Dimensionality) -> Dimensionality:
        return Dimensionality(**{**self.dimensions, **other.dimensions})

    def __str__(self) -> str:
        return str(self.dimensions)

    def __repr__(self) -> str:
        return f"{self} [hash: {self.hash}]"


NULL_DIMENSIONALITY = Dimensionality()


class RichConsoleSink(Sink):
    def __init__(self):
        self.serializer = LogSerializer()
        self.console = Console(force_terminal=True, width=250)

    def accept(self, context: MetricsContext):
        for serialized_content in self.serializer.serialize(context):
            self.console.print_json(serialized_content, indent=2)

    @staticmethod
    def name() -> str:  # pragma: no cover
        return "RichConsoleSink"


class RichEnvironment(LocalEnvironment):
    def __init__(self):
        self.sink = RichConsoleSink()


UNIT_MAPPING: dict[MetricUnit, Unit] = {
    MetricUnit.COUNT: Unit.COUNT,
    MetricUnit.MILLISECONDS: Unit.MILLISECONDS,
    MetricUnit.BYTES: Unit.BYTES,
    MetricUnit.SECONDS: Unit.SECONDS,
    MetricUnit.NONE: Unit.NONE,
}


@runtime_checkable
class MetricsBackend(Protocol):
    def emit(self, record: MetricRecord): ...
    def set_global_property(self, key: str, value: Any): ...
    def add_stack_trace(self, key: str): ...
    async def flush(self): ...


class EmfBackend:
    """Backend that emits metrics via AWS Embedded Metrics Format (EMF).

    Groups metrics with the same dimensions into a single EMF log line.
    """

    def __init__(
        self,
        *,
        environment_provider: Callable[..., Awaitable[Environment]] | None = None,
        base_dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
        logger_provider: Callable[[Callable[..., Awaitable[Environment]]], MitupMetricsLogger] = MitupMetricsLogger,
    ):
        from aws_embedded_metrics.environment.environment_detector import resolve_environment as default_resolver

        self._resolve_environment = environment_provider or default_resolver
        self._base_dimensions = base_dimensions or {}
        self._properties: dict[str, Any] = properties or {}
        self._logger_provider = logger_provider
        self._loggers: dict[Dimensionality, MitupMetricsLogger] = {}

    def _prepare_logger(
        self,
        dimensionality: Dimensionality | None = None,
        properties: dict[str, Any] | None = None,
    ) -> MitupMetricsLogger:
        logger = self._logger_provider(self._resolve_environment)
        logger.set_dimensions(use_default=False)
        logger.context.set_default_dimensions({})
        logger.flush_preserve_dimensions = True

        if dimensionality:
            logger.put_dimensions(dimensionality.dimensions)

        merged_properties = (properties or {}) | self._properties
        for key, value in merged_properties.items():
            logger.set_property(key, value)
        return logger

    def _get_logger(
        self,
        dimensionality: Dimensionality | None = None,
        properties: dict[str, Any] | None = None,
    ) -> MitupMetricsLogger:
        resolved = dimensionality or NULL_DIMENSIONALITY
        if resolved in self._loggers:
            for key, value in (properties or {}).items():
                self._loggers[resolved].set_property(key, value)
            return self._loggers[resolved]

        logger = self._prepare_logger(dimensionality, properties)
        self._loggers[resolved] = logger
        return logger

    def emit(self, record: MetricRecord):
        dimensions_dict = record.dimensions_dict
        merged_dims = {**self._base_dimensions, **dimensions_dict}
        dimensionality = Dimensionality(**merged_dims) if merged_dims else None

        emf_unit = UNIT_MAPPING.get(record.unit, Unit.COUNT)
        logger = self._get_logger(dimensionality, record.properties or None)
        logger.put_metric(record.name, record.value, emf_unit.value)

    def set_global_property(self, key: str, value: Any):
        self._properties[key] = value
        for logger in self._loggers.values():
            logger.set_property(key, value)

    def add_stack_trace(self, key: str):
        for logger in self._loggers.values():
            logger.add_stack_trace(key)

    async def flush(self):
        for logger in self._loggers.values():
            await logger.flush()


class NullBackend:
    """No-op backend for contexts where metrics emission is not needed."""

    def emit(self, record: MetricRecord): ...

    def set_global_property(self, key: str, value: Any): ...

    def add_stack_trace(self, key: str): ...

    async def flush(self): ...


def configure_emf_backend(config: MetricsConfig):
    """Set the global EMF configuration for the process.

    EMF relies on a global configuration singleton, so this must be called once
    at startup before any metrics are emitted.
    """
    metrics_config = get_config()

    metrics_config.namespace = config.namespace

    # If we have set the environment to Rich, lets use the Rich console as environment
    if config.environment is MetricsEnv.RICH:
        # Set the cached environment so the EMF library uses it
        EnvironmentCache.environment = RichEnvironment()

    metrics_config.environment = config.environment.value
