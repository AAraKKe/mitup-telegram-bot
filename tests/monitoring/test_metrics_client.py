"""Tests for the MetricsClient and NullBackend."""

from mitup_bot.monitoring import Feature, MetricKey, MetricsClient, MetricUnit
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


def make_client() -> MetricsClient:
    return make_test_metrics_client()


def test_emit_records_a_metric():
    client = make_client()
    client.emit("MyMetric", 42)

    assert len(client.records) == 1
    assert client.records[0].name == "MyMetric"
    assert client.records[0].value == 42


def test_emit_stores_unit():
    client = make_client()
    client.emit("MyMetric", 1.5, MetricUnit.MILLISECONDS)

    assert client.records[0].unit == MetricUnit.MILLISECONDS


def test_emit_stores_dimensions():
    client = make_client()
    client.emit("MyMetric", 1, dimensions={"Env": "test", "Region": "us-east-1"})

    dims = client.records[0].dimensions_dict
    assert dims["Env"] == "test"
    assert dims["Region"] == "us-east-1"


def test_emit_merges_base_dimensions():
    client = make_test_metrics_client(base_dimensions={"EventType": "cleanup"})
    client.emit("MyMetric", 1, dimensions={"Extra": "dim"})

    dims = client.records[0].dimensions_dict
    assert dims["EventType"] == "cleanup"
    assert dims["Extra"] == "dim"


def test_emit_base_dimensions_do_not_override_explicit():
    client = make_test_metrics_client(base_dimensions={"Key": "base"})
    client.emit("MyMetric", 1, dimensions={"Key": "explicit"})

    assert client.records[0].dimensions_dict["Key"] == "explicit"


def test_emit_stores_properties():
    client = make_client()
    client.emit("MyMetric", 1, properties={"UserId": 123})

    assert client.records[0].properties["UserId"] == 123


def test_records_accumulate_across_multiple_emits():
    client = make_client()
    client.emit("A", 1)
    client.emit("B", 2)
    client.emit("C", 3)

    assert len(client.records) == 3


def test_emit_feature_adds_feature_dimension():
    client = make_client()
    client.emit_feature(Feature.JOIN_MEETING)

    dims = client.records[0].dimensions_dict
    assert dims["Feature"] == str(Feature.JOIN_MEETING)


def test_emit_feature_with_named_args():
    client = make_client()
    # emit_feature forwards to emit; verify positional/keyword usage works
    client.emit_feature(Feature.JOIN_MEETING, value=2.0, name="Count", unit=MetricUnit.COUNT)

    assert len(client.records) == 1
    assert client.records[0].name == "Count"
    assert client.records[0].value == 2.0
    assert client.records[0].dimensions_dict["Feature"] == str(Feature.JOIN_MEETING)


def test_emit_global_emits_dimensionless_copy_with_base_dims_as_properties():
    client = make_test_metrics_client(base_dimensions={"EventType": "cleanup"})
    client.emit(MetricKey.FAULT, 1, MetricUnit.COUNT, emit_global=True)

    assert len(client.records) == 2

    dimensioned = MetricAssertions(client)
    dimensioned.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        dimensions={"EventType": "cleanup"},
        dimensions_exact=True,
    )
    # Global copy: no dimensions, EventType demoted to a searchable property.
    dimensioned.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        dimensions={},
        dimensions_exact=True,
        properties={"EventType": "cleanup"},
        properties_exact=True,
    )


def test_emit_global_defaults_off_emits_single_record():
    client = make_test_metrics_client(base_dimensions={"EventType": "cleanup"})
    client.emit(MetricKey.FAULT, 0, MetricUnit.COUNT)

    assert len(client.records) == 1
    assert client.records[0].dimensions_dict == {"EventType": "cleanup"}
    assert client.records[0].properties == {}


def test_emit_global_keeps_explicit_dimensions_on_copy():
    """The global copy drops only base_dimensions; explicitly passed dimensions survive."""
    client = make_test_metrics_client(base_dimensions={"EventType": "cleanup"})
    client.emit(MetricKey.FAULT, 1, dimensions={"Region": "eu"}, emit_global=True)

    global_record = next(r for r in client.records if "EventType" not in r.dimensions_dict)
    assert global_record.dimensions_dict == {"Region": "eu"}
    assert global_record.properties == {"EventType": "cleanup"}


def test_emit_global_without_base_dimensions_still_emits_two_records():
    client = make_client()
    client.emit(MetricKey.FAULT, 1, emit_global=True)

    assert len(client.records) == 2
    for record in client.records:
        assert record.dimensions_dict == {}
        assert record.properties == {}


async def test_flush_delegates_to_backend():
    """Flushing does not raise and does not clear records."""
    client = make_client()
    client.emit("MyMetric", 1)
    await client.flush()

    # Records are still available after flush (NullBackend discards nothing)
    assert len(client.records) == 1


def test_set_global_property_delegates_to_backend():
    from unittest.mock import MagicMock

    from mitup_bot.monitoring.backend import MetricsBackend

    backend = MagicMock(spec=MetricsBackend)
    client = MetricsClient(backend)

    client.set_global_property("request_id", "abc-123")

    backend.set_global_property.assert_called_once_with("request_id", "abc-123")


def test_metric_assertions_helper_works_with_client():
    client = make_client()
    client.emit(MetricKey.FAULT, 0)

    MetricAssertions(client).assert_emitted(name=MetricKey.FAULT, value=0)


def test_metric_assertions_assert_not_emitted():
    client = make_client()

    MetricAssertions(client).assert_not_emitted(name=MetricKey.FAULT)
