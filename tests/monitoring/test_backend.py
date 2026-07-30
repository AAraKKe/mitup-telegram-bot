"""Tests for the EmfBackend, MitupMetricsLogger, RichConsoleSink, and configure_emf_backend."""

import json
from datetime import UTC, datetime, timedelta
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
    logger.put_metric("DbPoolConnectionsInUse", 1)

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


def build_capturing_logger() -> tuple[MitupMetricsLogger, CapturingSink]:
    """A logger wired exactly as `EmfBackend._prepare_logger` wires one, over a capturing sink."""
    sink = CapturingSink()
    environment = CapturingEnvironment(sink)

    async def resolver() -> Environment:
        return environment

    logger = MitupMetricsLogger(resolver)
    logger.set_dimensions(use_default=False)
    logger.context.set_default_dimensions({})
    logger.flush_preserve_dimensions = True
    return logger, sink


async def test_flush_serializes_the_whole_document_when_the_context_holds_metrics():
    """The document a metric-bearing flush writes is what CloudWatch alarms and dashboard widgets
    read, so it is pinned key by key: any change to the dimensions, namespace, properties or
    values reaching the sink is a production incident and must fail here."""
    logger, sink = build_capturing_logger()
    logger.set_namespace("Mitup/Test")
    logger.put_dimensions({"Feature": "DbPool"})
    logger.set_property("run_id", "abc-123")
    logger.put_metric("DbPoolConnectionsInUse", 3.0, "Count")

    await logger.flush()

    assert len(sink.serialized) == 1
    payload = json.loads(sink.serialized[0])
    timestamp = payload["_aws"].pop("Timestamp")
    assert payload == {
        "Feature": "DbPool",
        "run_id": "abc-123",
        "DbPoolConnectionsInUse": 3.0,
        "_aws": {
            "CloudWatchMetrics": [
                {
                    "Dimensions": [["Feature"]],
                    "Metrics": [{"Name": "DbPoolConnectionsInUse", "Unit": "Count"}],
                    "Namespace": "Mitup/Test",
                }
            ]
        },
    }
    assert isinstance(timestamp, int)


async def test_flush_writes_nothing_when_the_context_holds_no_metrics():
    logger, sink = build_capturing_logger()
    logger.put_dimensions({"Feature": "DbPool"})
    logger.put_metric("DbPoolConnectionsInUse", 1)

    await logger.flush()
    await logger.flush()
    await logger.flush()

    assert len(sink.serialized) == 1


async def test_default_dimensions_stay_off_across_a_skipped_flush():
    """A flush that writes nothing leaves the context in place rather than replacing it with the
    copy whose `__init__` re-enables the EMF defaults, so the re-assertion guarding issue #202
    does not run. The next flush that does carry metrics must still serialize dimensionless."""
    logger, sink = build_capturing_logger()
    logger.put_dimensions({"Feature": "DbPool"})

    logger.put_metric("DbPoolConnectionsInUse", 1)
    await logger.flush()
    await logger.flush()
    await logger.flush()
    logger.put_metric("DbPoolConnectionsInUse", 2)
    await logger.flush()

    assert len(sink.serialized) == 2
    default_dimension_keys = {"LogGroup", "ServiceName", "ServiceType"}
    for line in sink.serialized:
        payload = json.loads(line)
        leaked = default_dimension_keys & payload.keys()
        assert not leaked, f"default dimensions leaked into EMF payload: {sorted(leaked)}"


async def test_flush_that_writes_nothing_still_refreshes_the_document_timestamp():
    """A context carries the timestamp it was built with and only a serialized flush builds the
    next one, so an idle logger would otherwise backdate its next document by the whole idle
    stretch — far enough to drop the datapoint out of the window an alarm evaluates."""
    logger, sink = build_capturing_logger()
    logger.put_dimensions({"Feature": "DbPool"})
    logger.put_metric("DbPoolConnectionsInUse", 1)
    await logger.flush()

    logger.context.set_timestamp(datetime.now(UTC) - timedelta(hours=3))
    await logger.flush()

    logger.put_metric("DbPoolConnectionsInUse", 2)
    await logger.flush()

    payload = json.loads(sink.serialized[-1])
    age_ms = int(round(datetime.now(UTC).timestamp() * 1000)) - payload["_aws"]["Timestamp"]
    assert age_ms < 60_000


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
