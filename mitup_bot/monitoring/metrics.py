import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from aws_embedded_metrics.config import get_config
from aws_embedded_metrics.environment import Environment
from aws_embedded_metrics.environment.environment_detector import EnvironmentCache, resolve_environment
from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.serializers.log_serializer import LogSerializer
from aws_embedded_metrics.sinks import Sink
from aws_embedded_metrics.unit import Unit
from rich.console import Console
from telegram import Update

from mitup_bot.config import MetricsConfig, MetricsEnv
from mitup_bot.monitoring import MetricKey


class RichConsoleSink(Sink):
    def __init__(self):
        self.serializer = LogSerializer()
        self.console = Console(force_terminal=True, width=250)

    def accept(self, context: MetricsContext) -> None:
        for serialized_content in self.serializer.serialize(context):
            self.console.print_json(serialized_content, indent=2)

    @staticmethod
    def name() -> str:  # pragma: no cover
        return "RichConsoleSink"


class RichEnvironment(LocalEnvironment):
    def __init__(self):
        self.sink = RichConsoleSink()


TLoggerProperties = dict[str, str | int | float | None]


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
    def or_null(dimensions: dict[str, str] | None) -> "Dimensionality":
        return Dimensionality() if dimensions is None else Dimensionality(**dimensions)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, value: object) -> bool:
        return self.hash == value.hash if isinstance(value, Dimensionality) else False

    def __add__(self, other: "Dimensionality") -> "Dimensionality":
        return Dimensionality(**{**self.dimensions, **other.dimensions})

    def __str__(self) -> str:
        return str(self.dimensions)

    def __repr__(self) -> str:
        return f"{self} [hash: {self.hash}]"


NULL_DIMENSIONALITY = Dimensionality()


class MitupMetricsEngine[TML: MitupMetricsLogger]:
    """
    The MitupMetricsEngine is the entity responsible for managing metrics with different dimensionalities and
    properties.

    It should be used as the entry point for emitting metrics in the Mitup Bot and holds the logic that ensures that
    metrics are emitted in the most cost effective way and in a flexible manner.

    When a metric is emitted, its dimensionality is determined by the dimensions required for the metric. The engine
    caches all seen dimensionalities reusing cached loggers to flush metrics with the same dimensionality in a
    single EMF log line.

    The `resolve_environment` function is used to determine the environment for the MetricsContext.
    The `logger_provider` parameter is a function that returns a new instance of a MitupMetricsLogger or a child
    class of it.
    The `properties` parameter is a dictionary with properties that should be set in all loggers created by the engine.
    """

    environment_provider: Callable[..., Awaitable[Environment]] | None = None

    def __init__(
        self,
        *,
        logger_provider: Callable[[Callable[..., Awaitable[Environment]]], TML],
        properties: TLoggerProperties | None = None,
    ):
        self.resolve_environment = self.__class__.environment_provider or resolve_environment
        self.loggers: dict[Dimensionality, TML] = {}
        self.properties: TLoggerProperties = properties or {}
        self.logger_provider = logger_provider

    def __prepare_logger(
        self,
        logger: TML,
        dimensionality: Dimensionality | None = None,
        properties: dict[str, Any] | None = None,
    ) -> MitupMetricsLogger:
        # Always remove the default dimensions, we don't want to include dimensions we don't control
        logger.set_dimensions(use_default=False)
        logger.context.set_default_dimensions({})
        logger.flush_preserve_dimensions = True

        if dimensionality:
            logger.put_dimensions(dimensionality.dimensions)

        properties = (properties or {}) | self.properties
        for key, value in properties.items():
            logger.set_property(key, value)
        return logger

    def get_logger(
        self,
        dimensions: Dimensionality | None = None,
        properties: TLoggerProperties | None = None,
    ) -> TML:
        """
        Generate a logger with the given properties and dimensions. If the logger already exists, return it.
        """
        # If the logger is already registered, return it
        dimensionality = dimensions or NULL_DIMENSIONALITY
        if dimensionality in self.loggers:
            for key, value in (properties or {}).items():
                self.loggers[dimensionality].set_property(key, value)
            return self.loggers[dimensionality]

        logger = self.logger_provider(self.resolve_environment)
        self.__prepare_logger(logger, dimensions, properties)

        self.loggers[dimensionality] = logger
        return logger

    def put_metric(
        self,
        name: str | MetricKey = MetricKey.COUNT,
        value: float = 1.0,
        unit: Unit = Unit.COUNT,
        dimensions: Dimensionality | None = None,
        properties: TLoggerProperties | None = None,
    ):
        logger = self.get_logger(dimensions, properties)
        logger.put_metric(str(name), value, unit.value)

    async def flush_metrics(self):
        for logger in self.loggers.values():
            await logger.flush()

    @contextmanager
    def auto_flush(self) -> Generator["MitupMetricsEngine", None, None]:
        """Context manager that allows using a logger and flush automatically from a synchronous context."""
        yield self
        asyncio.run(self.flush_metrics())

    @asynccontextmanager
    async def async_auto_flush(self) -> AsyncGenerator["MitupMetricsEngine", None]:
        """Async context manager that allows using a logger and flush automatically from an asynchronous context."""
        yield self
        await self.flush_metrics()

    def add_stack_trace(self):
        for logger in self.loggers.values():
            logger.add_stack_trace("exception")


def properties_from_update(update: Update) -> dict[str, Any]:
    """Create a dictionary with the properties from the provided Update."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    callback_data = update.callback_query.data if update.callback_query else None

    return {
        "ChatId": chat_id,
        "UserId": user_id,
        "CallbackData": callback_data,
        "Update": update.to_dict(),
    }


def configure_metrics(config: MetricsConfig):
    """Set the EMF configuration with the provided configuration.

    This is done globally for the entire process as EMF relies on a global configuration to ensure that
    metrics for a given process have the same behavior
    """
    metrics_config = get_config()

    metrics_config.namespace = config.namespace

    # If we have set the environment to Rich, lets use the Rich console as environment
    if config.environment is MetricsEnv.RICH:
        # Set the cached environment so the EMF library uses it
        EnvironmentCache.environment = RichEnvironment()

    metrics_config.environment = config.environment.value


# def create_metrics_from_update(update: Update) -> MitupMetricsLogger:
#     """Create a MetricsLogger with the provided Update."""
#     return __prepare_logger(properties=properties_from_update(update))
