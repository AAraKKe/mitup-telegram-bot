"""Tests for the EmfBackend, MitupMetricsLogger, RichConsoleSink, and configure_emf_backend."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from aws_embedded_metrics.environment import Environment
from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.serializers.log_serializer import LogSerializer
from aws_embedded_metrics.sinks import Sink

from mitup_bot.config import MetricsConfig, MetricsEnv
from mitup_bot.monitoring.backend import (
    NULL_DIMENSIONALITY,
    UNIT_MAPPING,
    Dimensionality,
    EmfBackend,
    MitupMetricsLogger,
    RichConsoleSink,
    RichEnvironment,
    configure_emf_backend,
)
from mitup_bot.monitoring.record import MetricRecord
from mitup_bot.monitoring.units import MetricUnit

# --- MitupMetricsLogger ---


def test_mitup_metrics_logger_init_disables_default_dimensions():
    resolver = AsyncMock()
    logger = MitupMetricsLogger(resolver)

    assert logger.context.should_use_default_dimensions is False


async def test_mitup_metrics_logger_flush_calls_super_and_stdout():
    resolver = AsyncMock()
    logger = MitupMetricsLogger(resolver)

    with (
        patch.object(MetricsLogger, "flush", new_callable=AsyncMock) as mock_super_flush,
        patch("mitup_bot.monitoring.backend.sys") as mock_sys,
    ):
        await logger.flush()
        mock_super_flush.assert_awaited_once()
        mock_sys.stdout.flush.assert_called_once()


class CapturingSink(Sink):
    """Sink that records every EMF line the logger serializes, for dimension assertions."""

    def __init__(self):
        self.serializer = LogSerializer()
        self.serialized: list[str] = []

    def accept(self, context: MetricsContext):
        self.serialized.extend(self.serializer.serialize(context))

    @staticmethod
    def name() -> str:
        return "CapturingSink"


class CapturingEnvironment(LocalEnvironment):
    """LocalEnvironment (LogGroup/ServiceName/ServiceType default to Unknown) with a capturing sink."""

    def __init__(self, sink: CapturingSink):
        self.sink = sink


async def test_consecutive_flushes_never_leak_default_dimensions():
    """Regression for issue #202: a long-lived logger flushed once per transaction must never
    emit the LogGroup/ServiceName/ServiceType EMF defaults. `MetricsLogger.flush()` re-enables
    them on the copied context, so without the re-assertion in `MitupMetricsLogger.flush()` the
    second flush leaks them as a duplicate metric series."""
    sink = CapturingSink()
    environment = CapturingEnvironment(sink)

    async def resolver() -> Environment:
        return environment

    logger = MitupMetricsLogger(resolver)
    # Mirror EmfBackend._prepare_logger: custom dimensions survive across per-transaction flushes.
    logger.flush_preserve_dimensions = True
    logger.put_dimensions({"Feature": "DbPool"})

    logger.put_metric("DbPoolConnectionsInUse", 1)
    await logger.flush()
    logger.put_metric("DbPoolConnectionsInUse", 2)
    await logger.flush()

    assert len(sink.serialized) == 2
    default_dimension_keys = {"LogGroup", "ServiceName", "ServiceType"}
    for line in sink.serialized:
        payload = json.loads(line)
        leaked = default_dimension_keys & payload.keys()
        assert not leaked, f"default dimensions leaked into EMF payload: {sorted(leaked)}"


# --- Dimensionality.__add__ ---


def test_dimensionality_add_merges_dimensions():
    dim_a = Dimensionality(env="prod")
    dim_b = Dimensionality(region="us-east-1")

    merged = dim_a + dim_b

    assert merged.dimensions == {"env": "prod", "region": "us-east-1"}


def test_dimensionality_add_right_overrides_left():
    dim_a = Dimensionality(key="old")
    dim_b = Dimensionality(key="new")

    merged = dim_a + dim_b

    assert merged.dimensions == {"key": "new"}


# --- RichConsoleSink ---


def test_rich_console_sink_init_creates_serializer_and_console():
    sink = RichConsoleSink()

    assert sink.serializer is not None
    assert sink.console is not None


def test_rich_console_sink_accept_serializes_and_prints():
    sink = RichConsoleSink()
    mock_context = MagicMock()
    sink.serializer = MagicMock()
    sink.serializer.serialize.return_value = ['{"key": "value"}']
    sink.console = MagicMock()

    sink.accept(mock_context)

    sink.serializer.serialize.assert_called_once_with(mock_context)
    sink.console.print_json.assert_called_once_with('{"key": "value"}', indent=2)


# --- RichEnvironment ---


def test_rich_environment_init_creates_rich_console_sink():
    env = RichEnvironment()

    assert isinstance(env.sink, RichConsoleSink)


# --- EmfBackend._prepare_logger ---


def test_prepare_logger_creates_logger_with_dimensions_and_properties():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider, properties={"global_key": "global_val"})
    dims = Dimensionality(env="test")

    logger = backend._prepare_logger(dimensionality=dims, properties={"local_key": "local_val"})

    assert logger is mock_logger
    provider.assert_called_once()
    mock_logger.set_dimensions.assert_called_once_with(use_default=False)
    mock_logger.context.set_default_dimensions.assert_called_once_with({})
    mock_logger.put_dimensions.assert_called_once_with(dims.dimensions)
    # Properties: local_key + global_key (global overrides via | operator)
    expected_calls = [("local_key", "local_val"), ("global_key", "global_val")]
    actual_calls = [(c.args[0], c.args[1]) for c in mock_logger.set_property.call_args_list]
    assert actual_calls == expected_calls


def test_prepare_logger_without_dimensionality_skips_put_dimensions():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)
    logger = backend._prepare_logger()

    assert logger is mock_logger
    mock_logger.put_dimensions.assert_not_called()


# --- EmfBackend._get_logger ---


def test_get_logger_caches_and_returns_same_logger():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)

    logger_first = backend._get_logger(Dimensionality(env="test"))
    logger_second = backend._get_logger(Dimensionality(env="test"))

    assert logger_first is logger_second
    # Provider called only once (cached on second call)
    provider.assert_called_once()


def test_get_logger_updates_properties_on_cached_logger():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)
    dims = Dimensionality(env="test")

    backend._get_logger(dims)
    mock_logger.reset_mock()
    backend._get_logger(dims, properties={"new_key": "new_val"})

    mock_logger.set_property.assert_called_once_with("new_key", "new_val")


def test_get_logger_without_dimensionality_uses_null():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)
    logger = backend._get_logger()

    assert logger is mock_logger
    assert NULL_DIMENSIONALITY in backend._loggers


# --- EmfBackend.emit ---


def test_emit_merges_base_dimensions_and_puts_metric():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider, base_dimensions={"Env": "test"})

    record = MetricRecord(
        name="TestMetric",
        value=42.0,
        unit=MetricUnit.COUNT,
        dimensions=frozenset({("Region", "us-east-1")}),
        properties={},
    )

    backend.emit(record)

    mock_logger.put_dimensions.assert_called_once_with({"Env": "test", "Region": "us-east-1"})
    mock_logger.put_metric.assert_called_once_with("TestMetric", 42.0, UNIT_MAPPING[MetricUnit.COUNT].value)


def test_emit_with_no_dimensions_uses_none_dimensionality():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)

    record = MetricRecord(
        name="TestMetric",
        value=1.0,
        unit=MetricUnit.MILLISECONDS,
        dimensions=frozenset(),
        properties={},
    )

    backend.emit(record)

    mock_logger.put_metric.assert_called_once_with("TestMetric", 1.0, UNIT_MAPPING[MetricUnit.MILLISECONDS].value)


# --- EmfBackend.set_global_property ---


def test_set_global_property_updates_all_loggers():
    mock_logger_a = MagicMock(spec=MitupMetricsLogger)
    mock_logger_a.context = MagicMock()
    mock_logger_b = MagicMock(spec=MitupMetricsLogger)
    mock_logger_b.context = MagicMock()

    call_count = 0

    def provider_factory(resolver):
        nonlocal call_count
        call_count += 1
        return mock_logger_a if call_count == 1 else mock_logger_b

    backend = EmfBackend(logger_provider=provider_factory)

    # Create two loggers with different dimensionalities
    backend._get_logger(Dimensionality(env="a"))
    backend._get_logger(Dimensionality(env="b"))

    backend.set_global_property("request_id", "abc-123")

    mock_logger_a.set_property.assert_called_with("request_id", "abc-123")
    mock_logger_b.set_property.assert_called_with("request_id", "abc-123")
    assert backend._properties["request_id"] == "abc-123"


# --- EmfBackend.flush ---


async def test_flush_awaits_flush_on_all_loggers():
    mock_logger = MagicMock(spec=MitupMetricsLogger)
    mock_logger.context = MagicMock()
    mock_logger.flush = AsyncMock()
    provider = MagicMock(return_value=mock_logger)

    backend = EmfBackend(logger_provider=provider)
    backend._get_logger()

    await backend.flush()

    mock_logger.flush.assert_awaited_once()


# --- configure_emf_backend ---


def test_configure_emf_backend_sets_rich_environment_when_rich():
    from aws_embedded_metrics.environment.environment_detector import EnvironmentCache

    original = EnvironmentCache.environment
    try:
        config = MetricsConfig(namespace="test-namespace", environment=MetricsEnv.RICH)

        configure_emf_backend(config)

        assert isinstance(EnvironmentCache.environment, RichEnvironment)
    finally:
        EnvironmentCache.environment = original


def test_configure_emf_backend_does_not_set_rich_environment_when_not_rich():
    from aws_embedded_metrics.config import get_config
    from aws_embedded_metrics.environment.environment_detector import EnvironmentCache

    # Reset to None first
    EnvironmentCache.environment = None

    config = MetricsConfig(namespace="test-namespace", environment=MetricsEnv.STDOUT)

    configure_emf_backend(config)

    emf_config = get_config()
    assert emf_config.namespace == "test-namespace"
    assert EnvironmentCache.environment is None
