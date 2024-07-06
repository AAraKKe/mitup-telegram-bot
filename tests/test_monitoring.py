from typing import Any, cast

import freezegun
import pytest
from aws_embedded_metrics.environment.environment_detector import resolve_environment
from aws_embedded_metrics.unit import Unit
from rich.console import Console
from telegram import Update

from mitup_bot import monitoring
from mitup_bot.config import MetricsConfig, MetricsEnv
from mitup_bot.monitoring.metrics import MitupMetricsLogger, RichConsoleSink, RichEnvironment
from tests.helpers import StubMetrics, UpdateRequest


def test_get_metrics_from_update(update: Update):
    metrics = monitoring.create_metrics_from_update(update)

    assert update.effective_user is not None
    assert update.effective_chat is not None
    assert update.callback_query is None

    assert metrics.context.properties["UserId"] == update.effective_user.id
    assert metrics.context.properties["ChatId"] == update.effective_chat.id
    assert metrics.context.properties["CallbackData"] is None


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True, ids=["callback_query"])
def test_get_metrics_from_update_with_callback_query(update: Update):
    metrics = monitoring.create_metrics_from_update(update)

    assert update.effective_user is not None
    assert update.effective_chat is not None
    assert update.callback_query is not None

    assert metrics.context.properties["UserId"] == update.effective_user.id
    assert metrics.context.properties["ChatId"] == update.effective_chat.id
    assert metrics.context.properties["CallbackData"] == update.callback_query.data


@pytest.mark.parametrize(
    "properties", [None, {"PropName1": "PropValue1", "PropName2": 123}], ids=["no_properties", "with_properties"]
)
@pytest.mark.parametrize(
    "dimensions", [None, {"DimName1": "DimValue1", "DimName2": "DimValue2"}], ids=["no_dimensions", "with_dimensions"]
)
def test_context_manager(properties: dict[str, Any] | None, dimensions: dict[str, str] | None):
    with monitoring.metrics_context(dimensions=dimensions, properties=properties) as logger:
        logger = cast(StubMetrics, logger)

        logger.put_metric("MyMetric", 123.123, unit=Unit.COUNT.value)
        logger.put_metric("MyTime", 12345, unit=Unit.MILLISECONDS.value)

    # Assert that the metric has been emitted with the dimensions requested within the context manager
    # Asserting emission ensures flush has been called when outside the context manager
    logger.assert_metrics_emited(
        ["MyMetric", "MyTime"],
        [123.123, 12345],
        [Unit.COUNT, Unit.MILLISECONDS],
        dimensions=dimensions,
        properties=properties,
    )


async def test_async_context_manager():
    dimensions = {"Name1": "Value1", "Name2": "Value2"}
    properties = {"Name3": "Value3", "Name4": 123}

    async with monitoring.async_metrics_context(dimensions=dimensions, properties=properties) as logger:
        logger = cast(StubMetrics, logger)

        logger.put_metric("MyMetric", 123.123, unit=Unit.COUNT.value)
        logger.put_metric("MyTime", 12345, unit=Unit.MILLISECONDS.value)

    # Assert that the metric has been emitted with the dimensions requested within the context manager
    # Asserting emission ensures flush has been called when outside the context manager
    logger.assert_metrics_emited(
        ["MyMetric", "MyTime"],
        [123.123, 12345],
        [Unit.COUNT, Unit.MILLISECONDS],
        dimensions=dimensions,
        properties=properties,
    )


def test_metrics_key_preffix():
    key = monitoring.MetricKey.TIME
    assert key.with_prefix("MyPrefix") == "MyPrefix/Time"
    assert key.with_prefix("MyPrefix", separator=":") == "MyPrefix:Time"


async def test_rich_environment():
    # Need to convifugre monitoring with a custom logger to test the output and override the global test configuration
    # from contest.py
    monitoring.configure_metrics(
        MetricsConfig(namespace="MyNamespace", environment=MetricsEnv.RICH),
        factory=lambda: MitupMetricsLogger(resolve_environment),
    )

    async with monitoring.async_metrics_context() as logger:
        environment = cast(RichEnvironment, await logger.resolve_environment())
        sink = cast(RichConsoleSink, environment.sink)
        # Remove colors from terminal to test output
        sink.console = Console(force_interactive=False, force_terminal=False)

        with sink.console.capture() as capture, freezegun.freeze_time("2024-01-01 12:00:00"):
            logger.put_metric("MyMetric", 1.0)
            captured_timestamp = logger.context.meta["Timestamp"]
            await logger.flush()

        expected_formatted_text = (
            "{\n"
            '  "_aws": {\n'
            f'    "Timestamp": {captured_timestamp},\n'
            '    "CloudWatchMetrics": [\n'
            "      {\n"
            '        "Dimensions": [],\n'
            '        "Metrics": [\n'
            "          {\n"
            '            "Name": "MyMetric",\n'
            '            "Unit": "None"\n'
            "          }\n"
            "        ],\n"
            '        "Namespace": "MyNamespace"\n'
            "      }\n"
            "    ]\n"
            "  },\n"
            '  "MyMetric": 1.0\n'
            "}\n"
        )

        assert capture.get() == expected_formatted_text
