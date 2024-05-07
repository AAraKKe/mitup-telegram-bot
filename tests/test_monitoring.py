from typing import Any, cast

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update

from mitup_bot import monitoring
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


@pytest.mark.asyncio
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
