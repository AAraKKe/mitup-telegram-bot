from typing import Any

import pytest

from mitup_bot.custom_context import (
    ContextData,
    ContextId,
)
from mitup_bot.exceptions import ContextPropertyConversionError, ContextPropertyNotSetError
from mitup_bot.monitoring import Feature, MetricUnit
from tests.helpers import AnyFloat, StubMitupContext
from tests.helpers.monitoring import MetricAssertions


def test_add_and_remove_context(context: StubMitupContext):
    assert context.user_data is not None

    assert ContextId.EDIT_MEETING_LOCATION_NAME not in context.user_data.registry

    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert context.user_data.registry[ContextId.EDIT_MEETING_LOCATION_NAME].meeting_id == 123

    context.clean_user_data([ContextId.EDIT_MEETING_LOCATION_NAME])
    assert ContextId.EDIT_MEETING_LOCATION_NAME not in context.user_data.registry


def test_get_user_data_property_invalid_type(context: StubMitupContext):
    context.user_data.registry[ContextId.EDIT_MEETING_LOCATION_NAME] = ContextData(meeting_id="broken")  # type: ignore

    with pytest.raises(ContextPropertyConversionError):
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME):
            pass


def test_meeting_id_context_manager(context: StubMitupContext):
    assert context.user_data is not None

    # Set values before context manager
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert ContextId.EDIT_MEETING_LOCATION_NAME in context.user_data.registry

    # Read from within the context manager
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME) as meeting_id:
        assert meeting_id == 123

    # The context has been removed from the registry
    assert ContextId.EDIT_MEETING_LOCATION_NAME not in context.user_data.registry


def test_error_raised_if_property_requested_but_not_set(context: StubMitupContext):
    assert context.user_data is not None

    context.user_data.registry[ContextId.EDIT_MEETING_LOCATION_NAME] = ContextData()

    with pytest.raises(ContextPropertyNotSetError):
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME):
            pass


def test_context_manager_error_clean_user_data(context: StubMitupContext):
    assert context.user_data is not None

    # Set values before context manager
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert ContextId.EDIT_MEETING_LOCATION_NAME in context.user_data.registry

    # Read from within the context manager
    with pytest.raises(ValueError):
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME):
            raise ValueError("Test error")

    # The context has been removed from the registry
    assert ContextId.EDIT_MEETING_LOCATION_NAME not in context.user_data.registry


def test_context_manager_error_does_not_clean_data_if_requested(context: StubMitupContext):
    assert context.user_data is not None

    # Set values before context manager
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert ContextId.EDIT_MEETING_LOCATION_NAME in context.user_data.registry

    # Read from within the context manager
    with pytest.raises(ValueError):
        with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, ensure_clean=False):
            raise ValueError("Test error")

    # The context has been removed from the registry
    assert ContextId.EDIT_MEETING_LOCATION_NAME in context.user_data.registry


def test_context_manager_error_does_not_clean_data_if_not_requested(context: StubMitupContext):
    assert context.user_data is not None

    # Set values before context manager
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    # Read from within the context manager
    with context.meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, ensure_clean=False) as meeting_id:
        assert meeting_id == 123

    # Meeting Id has not been removed from the registry
    assert context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)


def test_context_has_meeting_id(context: StubMitupContext):
    assert context.user_data is not None

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    assert context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)

    context.clean_all_user_data()
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME)


@pytest.mark.parametrize(
    "properties", [None, {"PropName1": "PropValue1", "PropName2": 123}], ids=["no_properties", "with_properties"]
)
@pytest.mark.parametrize(
    "dimensions", [None, {"DimName1": "DimValue1", "DimName2": "DimValue2"}], ids=["no_dimensions", "with_dimensions"]
)
@pytest.mark.parametrize(
    "with_update_properties", [True, False], ids=["with_context_properties", "without_context_properties"]
)
@pytest.mark.parametrize(
    "with_handler_properties", [True, False], ids=["with_handler_properties", "without_handler_properties"]
)
async def test_metrics_emitted(
    context: StubMitupContext,
    metrics: MetricAssertions,
    dimensions: None | dict[str, str],
    properties: None | dict[str, Any],
    with_update_properties: bool,
    with_handler_properties: bool,
):
    context.emit_metric(
        "test_metric",
        value=123,
        dimensions=dimensions,
        properties=properties,
        include_handler_properties=with_handler_properties,
        include_update_properties=with_update_properties,
    )

    await context.flush_metrics()

    # Subset match on dimensions/properties: the record includes extra auto-added properties
    # (update properties like user_id, chat_id). We check only the explicitly-passed ones.
    metrics.assert_emitted(
        name="test_metric",
        value=123,
        dimensions=dimensions,
        properties=properties,
    )


async def test_feature_metric_emitted_with_proper_dimension(context: StubMitupContext, metrics: MetricAssertions):
    context.put_feature_metric(
        Feature.CREATE_MEETING,
        name="MyMetric",
        value=123,
        dimensions={"DimeName": "DimeValue"},
        properties={"PropName": "PropValue"},
    )

    await context.flush_metrics()

    metrics.assert_emitted(
        name="MyMetric",
        value=123,
        dimensions={"DimeName": "DimeValue", "Feature": Feature.CREATE_MEETING.value},
        properties={"PropName": "PropValue"},
    )


async def test_timing_metrics(context: StubMitupContext, metrics: MetricAssertions):
    with context.with_time_metric("MyMetric"):
        pass

    await context.flush_metrics()

    metrics.assert_emitted(
        name="MyMetricTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
    )


async def test_timing_metrics_with_handler_properties(context: StubMitupContext, metrics: MetricAssertions):
    context.prepare_handler_metrics({"HandlerProp": "HandlerValue"})

    with context.with_time_metric("MyMetric", handler_metrics=True):
        pass

    await context.flush_metrics()

    # Handler identity rides as an EMF property, never as a dimension.
    metrics.assert_emitted(
        name="MyMetricTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
        dimensions={},
        dimensions_exact=True,
        properties={"HandlerProp": "HandlerValue"},
    )


def test_prepare_handler_metrics_empty_dict_is_noop(context: StubMitupContext):
    # Passing an empty dict must not alter _handler_properties
    context.prepare_handler_metrics({})

    # The handler properties must remain empty
    assert context._handler_properties == {}


async def test_emit_metric_attaches_handler_identity_as_properties_not_dimensions(
    context: StubMitupContext, metrics: MetricAssertions
):
    # Handler identity must ride as EMF properties and never inflate the dimension set — each
    # distinct dimension set is a separately billed CloudWatch series (issue #205).
    context.prepare_handler_metrics({"Handler": "SomeHandler", "HandlerType": "Callback"})

    context.emit_metric(
        "handler_metric",
        value=5.0,
        include_update_properties=False,
    )

    await context.flush_metrics()

    # Exactly one dimensionless record carrying the handler identity as properties.
    metrics.assert_emitted(
        name="handler_metric",
        value=5.0,
        dimensions={},
        dimensions_exact=True,
        properties={"Handler": "SomeHandler", "HandlerType": "Callback"},
        times=1,
    )
    # And no record ever carries the handler identity as a dimension.
    metrics.assert_not_emitted(name="handler_metric", dimensions={"Handler": "SomeHandler"})
