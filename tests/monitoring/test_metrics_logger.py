import pytest
from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.unit import Unit

from mitup_bot.monitoring import MitupMetricsLogger
from tests.helpers.monitoring import InMemorySink, TSinkContainer


@pytest.fixture
def logger() -> tuple[MitupMetricsLogger, TSinkContainer]:
    container: TSinkContainer = []
    sink = InMemorySink(container)

    async def environment():
        env = LocalEnvironment()
        env.sink = sink
        return env

    return MitupMetricsLogger(environment), container


def test_logger_has_no_default_dimensions(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    # Important to ensure we are not emitting dimensions we do not need
    assert logger[0].context.default_dimensions == {}


def test_logger_put_metric_does_not_flush(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    logger[0].put_metric("key", 42, Unit.COUNT.value)

    assert logger[1] == []


async def test_logger_put_metric_flushes(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    logger[0].put_metric("key", 42, Unit.COUNT.value)
    await logger[0].flush()

    container = logger[1]
    assert len(container) == 1
    assert container[0]["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "key"


async def test_logger_flush_does_not_add_default_dimensions(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    logger[0].put_metric("key", 42, Unit.COUNT.value)
    await logger[0].flush()

    container = logger[1]
    assert container[0]["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == []


async def test_logger_put_dimensions(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    logger[0].put_dimensions({"Dim1": "DimValue"})
    logger[0].put_metric("key", 42, Unit.COUNT.value)
    await logger[0].flush()

    container = logger[1]
    assert container[0]["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Dim1"]]
    assert container[0]["Dim1"] == "DimValue"


async def test_logger_set_property(logger: tuple[MitupMetricsLogger, TSinkContainer]):
    logger[0].set_property("prop1", "propValue")
    logger[0].put_metric("key", 42, Unit.COUNT.value)
    await logger[0].flush()

    container = logger[1]
    assert container[0]["prop1"] == "propValue"
    assert container[0]["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == []
