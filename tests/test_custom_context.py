from typing import Any

import pytest
from aws_embedded_metrics.unit import Unit

from mitup_bot.custom_context import (
    ContextData,
    ContextId,
)
from mitup_bot.exceptions import ContextPropertyConversionError, ContextPropertyNotSetError
from mitup_bot.monitoring import Feature
from tests.helpers import AnyFloat, StubMitupContext


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
    "with_handler_dimensions", [True, False], ids=["with_handler_dimensions", "without_handler_dimensions"]
)
async def test_metrics_emitted(
    context: StubMitupContext,
    dimensions: None | dict[str, str],
    properties: None | dict[str, Any],
    with_update_properties: bool,
    with_handler_dimensions: bool,
):
    context.emit_metric(
        "test_metric",
        value=123,
        dimensions=dimensions,
        properties=properties,
        include_handler_dimensions=with_handler_dimensions,
        include_update_properties=with_update_properties,
    )

    await context.flush_metrics()

    context.metrics_engine.assert_metrics_emited(
        ["test_metric"],
        [123],
        dimensions=dimensions,
        properties=properties or {},
        add_update_properties=with_update_properties,
        add_handler_dimensions=with_handler_dimensions,
    )


async def test_feature_metric_emitted_with_proper_dimension(context: StubMitupContext):
    context.put_feature_metric(
        Feature.CREATE_MEETING,
        name="MyMetric",
        value=123,
        dimensions={"DimeName": "DimeValue"},
        properties={"PropName": "PropValue"},
    )

    await context.flush_metrics()

    context.metrics_engine.assert_metrics_emited(
        ["MyMetric"],
        [123],
        dimensions={"DimeName": "DimeValue", "Feature": Feature.CREATE_MEETING.value},
        properties={"PropName": "PropValue"},
        add_handler_dimensions=False,
    )


async def test_timing_metrics(context: StubMitupContext):
    with context.with_time_metric("MyMetric"):
        pass

    await context.flush_metrics()

    context.metrics_engine.assert_metrics_emited(["MyMetricTime"], [AnyFloat()], [Unit.MILLISECONDS])


async def test_timing_metrics_with_handler_dimensions(context: StubMitupContext):
    context.prepare_handler_metrics({"HandlerDim": "HandlerValue"})

    with context.with_time_metric("MyMetric", handler_metrics=True):
        pass

    await context.flush_metrics()

    context.metrics_engine.assert_metrics_emited(
        ["MyMetricTime"], [AnyFloat()], [Unit.MILLISECONDS], dimensions={"HandlerDim": "HandlerValue"}
    )


def test_prepare_handler_metrics_empty_dict_is_noop(context: StubMitupContext):
    # Passing an empty dict must not alter handler_dimensionality
    from mitup_bot.monitoring import NULL_DIMENSIONALITY

    context.prepare_handler_metrics({})

    # The handler dimensionality must remain the null (empty) dimensionality
    assert context.handler_dimensionality == NULL_DIMENSIONALITY


async def test_emit_metric_global_also_emits_under_null_dimensionality(context: StubMitupContext):
    # emit_global=True must emit the metric under its own dimensionality AND also under
    # NULL_DIMENSIONALITY (for global CloudWatch aggregation).
    # We use a dimension on the primary call so both loggers are distinct, allowing
    # independent assertions.
    context.emit_metric(
        "global_metric",
        value=5.0,
        dimensions={"MyDim": "MyVal"},
        emit_global=True,
        include_handler_dimensions=False,
        include_update_properties=False,
    )

    await context.flush_metrics()

    # The primary metric is emitted under the named dimension
    context.metrics_engine.assert_metrics_emited(
        ["global_metric"],
        [5.0],
        dimensions={"MyDim": "MyVal"},
        add_handler_dimensions=False,
        add_update_properties=False,
    )
    # The global copy is emitted under NULL_DIMENSIONALITY (no dimensions at all)
    context.metrics_engine.assert_metrics_emited(
        ["global_metric"],
        [5.0],
        dimensions=None,
        add_handler_dimensions=False,
        add_update_properties=False,
    )
