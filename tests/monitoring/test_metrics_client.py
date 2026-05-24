"""Tests for the MetricsClient and NullBackend."""

from mitup_bot.monitoring import Feature, MetricKey, MetricsClient, MetricUnit
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


def _client() -> MetricsClient:
    return make_test_metrics_client()


def test_emit_records_a_metric():
    client = _client()
    client.emit("MyMetric", 42)

    assert len(client.records) == 1
    assert client.records[0].name == "MyMetric"
    assert client.records[0].value == 42


def test_emit_stores_unit():
    client = _client()
    client.emit("MyMetric", 1.5, MetricUnit.MILLISECONDS)

    assert client.records[0].unit == MetricUnit.MILLISECONDS


def test_emit_stores_dimensions():
    client = _client()
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
    client = _client()
    client.emit("MyMetric", 1, properties={"UserId": 123})

    assert client.records[0].properties["UserId"] == 123


def test_records_accumulate_across_multiple_emits():
    client = _client()
    client.emit("A", 1)
    client.emit("B", 2)
    client.emit("C", 3)

    assert len(client.records) == 3


def test_emit_feature_adds_feature_dimension():
    client = _client()
    client.emit_feature(Feature.JOIN_MEETING)

    dims = client.records[0].dimensions_dict
    assert dims["Feature"] == str(Feature.JOIN_MEETING)


def test_emit_feature_with_named_args():
    client = _client()
    # emit_feature forwards to emit; verify positional/keyword usage works
    client.emit_feature(Feature.JOIN_MEETING, value=2.0, name="Count", unit=MetricUnit.COUNT)

    assert len(client.records) == 1
    assert client.records[0].name == "Count"
    assert client.records[0].value == 2.0
    assert client.records[0].dimensions_dict["Feature"] == str(Feature.JOIN_MEETING)


async def test_flush_delegates_to_backend():
    """Flushing does not raise and does not clear records."""
    client = _client()
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
    client = _client()
    client.emit(MetricKey.FAULT, 0)

    MetricAssertions(client).assert_emitted(name=MetricKey.FAULT, value=0)


def test_metric_assertions_assert_not_emitted():
    client = _client()

    MetricAssertions(client).assert_not_emitted(name=MetricKey.FAULT)


# ---------------------------------------------------------------------------
# update_fields_from_update — branch coverage
# ---------------------------------------------------------------------------


def test_update_fields_from_update_no_effective_user():
    """An Update with no user omits the 'user' key (branch 25->31)."""
    from telegram import Update

    from mitup_bot.monitoring.client import update_fields_from_update

    result = update_fields_from_update(Update(update_id=1))

    assert "user" not in result
