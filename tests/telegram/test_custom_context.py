import datetime as dt
from typing import Any

import pytest
from telegram import Chat, Location, Message, Update
from telegram import User as TgUser

from mitup_bot.custom_context import (
    ContextData,
    ContextId,
    fault_fields_from_update,
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
    "with_handler_properties", [True, False], ids=["with_handler_properties", "without_handler_properties"]
)
async def test_metrics_emitted(
    context: StubMitupContext,
    metrics: MetricAssertions,
    dimensions: None | dict[str, str],
    properties: None | dict[str, Any],
    with_handler_properties: bool,
):
    context.emit_metric(
        "test_metric",
        value=123,
        dimensions=dimensions,
        properties=properties,
        include_handler_properties=with_handler_properties,
    )

    await context.flush_metrics()

    # Subset match on dimensions/properties: the record may carry the handler identity on top of
    # the explicitly-passed ones, which are all this asserts.
    metrics.assert_emitted(
        name="test_metric",
        value=123,
        dimensions=dimensions,
        properties=properties,
    )


async def test_emit_metric_carries_no_update_snapshot(context: StubMitupContext, metrics: MetricAssertions):
    # What the user did is described by the structlog lines bound to the same update_id. A snapshot
    # of the update on the record tells an alarm nothing and rides every emission of the flush
    # window, so nothing but what the caller passed may reach the record.
    context.emit_metric("snapshot_free_metric")

    await context.flush_metrics()

    metrics.assert_emitted(name="snapshot_free_metric", properties={}, properties_exact=True)


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


async def test_timing_metrics_omit_the_handler_identity(context: StubMitupContext, metrics: MetricAssertions):
    context.prepare_handler_metrics({"HandlerProp": "HandlerValue"})

    with context.with_time_metric("MyMetric"):
        pass

    await context.flush_metrics()

    # A timed call is not the handler invocation: the timing series stays dimensionless and free of
    # the handler identity, which the handler's own metrics carry.
    metrics.assert_emitted(
        name="MyMetricTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
        dimensions={},
        dimensions_exact=True,
    )
    metrics.assert_not_emitted(name="MyMetricTime", properties={"HandlerProp": "HandlerValue"})
    metrics.assert_not_emitted(name="MyMetricFault", properties={"HandlerProp": "HandlerValue"})


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

    context.emit_metric("handler_metric", value=5.0)

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


async def test_timing_metrics_success_emits_fault_zero(context: StubMitupContext, metrics: MetricAssertions):
    with context.with_time_metric("MyMetric"):
        pass

    await context.flush_metrics()

    metrics.assert_emitted(
        name="MyMetricTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
    )
    metrics.assert_emitted(name="MyMetricFault", value=0)


async def test_timing_metrics_emit_even_when_the_timed_call_raises(
    context: StubMitupContext, metrics: MetricAssertions
):
    with pytest.raises(ValueError, match="boom"):
        with context.with_time_metric("MyMetric"):
            raise ValueError("boom")

    await context.flush_metrics()

    metrics.assert_emitted(
        name="MyMetricTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
    )
    metrics.assert_emitted(name="MyMetricFault", value=1)


async def test_timing_metrics_keep_each_call_outcome_on_its_own_fault_sample(
    context: StubMitupContext, metrics: MetricAssertions
):
    # A flush window batches every dimensionless emission into one EMF document, where values
    # accumulate into an array but properties overwrite. The per-call outcome must therefore live
    # on the fault series — one 0/1 sample per call — and never on a property of the timing metric.
    with context.with_time_metric("MyMetric"):
        pass
    with pytest.raises(ValueError, match="boom"):
        with context.with_time_metric("MyMetric"):
            raise ValueError("boom")

    await context.flush_metrics()

    metrics.assert_emitted(name="MyMetricFault", value=0, times=1)
    metrics.assert_emitted(name="MyMetricFault", value=1, times=1)
    metrics.assert_emitted(name="MyMetricTime", unit=MetricUnit.MILLISECONDS, times=2)
    for outcome in (True, False):
        metrics.assert_not_emitted(name="MyMetricTime", properties={"Success": outcome})


async def test_user_data_mutations_emit_no_metrics(context: StubMitupContext, metrics: MetricAssertions):
    context.store_meeting_id(ContextId.EDIT_MEETING_LOCATION_NAME, 123)
    context.store_text(ContextId.EDIT_MEETING_LOCATION_NAME, "raw user text")
    context.clean_user_data([ContextId.EDIT_MEETING_LOCATION_NAME])

    await context.flush_metrics()

    metrics.assert_not_emitted(name="StoredMeetingId")
    metrics.assert_not_emitted(name="StoredContextText")
    metrics.assert_not_emitted(name="CleanUserData")


def test_fault_fields_carry_the_trigger_and_its_context():
    user = TgUser(id=42, first_name="Ada", is_bot=False, username="ada_l")
    chat = Chat(id=7, type=Chat.PRIVATE)
    message = Message(message_id=99, date=dt.datetime.now(dt.UTC), chat=chat, from_user=user, text="/start Madrid")

    fields = fault_fields_from_update(Update(update_id=5, message=message))

    assert fields == {
        "update_id": 5,
        "tg_user_id": 42,
        "username": "ada_l",
        "chat_id": 7,
        "message_id": 99,
        "trigger_text": "/start Madrid",
    }


def test_fault_fields_carry_a_location_trigger():
    user = TgUser(id=42, first_name="Ada", is_bot=False)
    chat = Chat(id=7, type=Chat.PRIVATE)
    location = Location(longitude=1.5, latitude=41.2)
    message = Message(message_id=99, date=dt.datetime.now(dt.UTC), chat=chat, from_user=user, location=location)

    fields = fault_fields_from_update(Update(update_id=5, message=message))

    assert fields["location"] == {"latitude": 41.2, "longitude": 1.5}
    assert fields["trigger_text"] is None
