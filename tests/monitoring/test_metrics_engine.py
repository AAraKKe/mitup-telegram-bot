import pytest
from aws_embedded_metrics.unit import Unit

from mitup_bot import monitoring
from mitup_bot.monitoring.metrics import (
    Dimensionality,
    TLoggerProperties,
)
from mitup_bot.utils.mitup_types import TMitupEngine
from tests.helpers import StubMetrics, StubMetricsEngine


@pytest.fixture
def stub_engine() -> StubMetricsEngine:
    return StubMetricsEngine(logger_provider=lambda ep: StubMetrics())


def build_engine(properties: TLoggerProperties | None = None) -> TMitupEngine:
    if properties:
        return monitoring.MitupMetricsEngine(logger_provider=lambda ep: StubMetrics(), properties=properties)
    else:
        return monitoring.MitupMetricsEngine(logger_provider=lambda ep: StubMetrics())


def test_engine_records_loggers():
    engine = monitoring.MitupMetricsEngine(logger_provider=lambda ep: StubMetrics())
    logger1 = engine.get_logger(Dimensionality(dim1="dim1"))
    logger2 = engine.get_logger(Dimensionality(dim1="dim1"))
    logger3 = engine.get_logger(Dimensionality(dim1="dim2"))

    assert logger1 is logger2
    assert logger1 is not logger3


def test_build_bare_engine_has_no_properties():
    engine = build_engine()
    logger = engine.get_logger()

    assert logger.context.properties == {}


async def test_flush_metrics_without_metrics(stub_engine: StubMetricsEngine):
    # Set some properties on the

    # Flushing the metrics does not emit anything
    await stub_engine.flush_metrics()

    assert stub_engine.container == []

    # But adding a metric emitts it
    stub_engine.put_metric("MyMetric", 123, unit=Unit.MILLISECONDS)
    await stub_engine.flush_metrics()
    assert stub_engine.container != []


async def test_put_metrics_does_not_flush(stub_engine: StubMetricsEngine):
    stub_engine.put_metric(
        name="MyMetric",
        value=123,
        unit=Unit.MILLISECONDS,
    )

    # Just calling put does not emit the metric
    stub_engine.assert_metrics_not_emited(["MyMetric"], [123])

    # But it is part of the context
    # The first context is the MitupContext and the second context is the MetricsContext ;)
    assert "MyMetric" in stub_engine.get_logger().context.metrics
    metric_in_context = stub_engine.get_logger().context.metrics["MyMetric"]
    assert Unit.MILLISECONDS.value == metric_in_context.unit
    assert metric_in_context.values == [123]


def test_engine_flushes_from_context_manager(stub_engine: StubMetricsEngine):
    with stub_engine.auto_flush():
        stub_engine.put_metric("MyMetric", 123, unit=Unit.MILLISECONDS)

    stub_engine.assert_metrics_emited(["MyMetric"], [123], [Unit.MILLISECONDS])


async def test_engine_flushes_from_context_async_manager(stub_engine: StubMetricsEngine):
    async with stub_engine.async_auto_flush():
        stub_engine.put_metric("MyMetric", 123, unit=Unit.MILLISECONDS)

    stub_engine.assert_metrics_emited(["MyMetric"], [123], [Unit.MILLISECONDS])
