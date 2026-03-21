# Metrics Assertions

## Overview

Metrics are tested via `StubMetrics` and `StubMetricsEngine` from `tests.helpers.monitoring`. These use an `InMemorySink` that captures emitted CloudWatch EMF metrics for assertion.

`StubMetricsEngine` is available via `context.metrics_engine` after calling `call_handler`.

## Important: Unit.MILLISECONDS for TIME metrics

When asserting `MetricKey.TIME`, you **must** explicitly pass `units=[Unit.MILLISECONDS]`. The default unit is `Unit.COUNT`, which causes a silent mismatch. Always import:

```python
from aws_embedded_metrics.unit import Unit
```

## StubMetricsEngine assertion methods

### `assert_handler_metrics_emitted(names, values=None, units=None, exception=None, times=1)`

The most common assertion for handler tests. Automatically adds handler dimensions and update properties.

```python
context.metrics_engine.assert_handler_metrics_emitted(
    [MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [0, AnyFloat(), 0],
    [Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
)
```

With an exception:
```python
context.metrics_engine.assert_handler_metrics_emitted(
    [MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [1, AnyFloat(), 0],
    [Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
    exception=UserNotFound,
)
```

### `assert_metrics_emited(names, values, units, namespace, dimensions, properties, exception, times, negative_case, add_handler_dimensions, add_update_properties)`

Full-control assertion. Use when you need custom dimensions, properties, or to disable automatic handler dimensions.

```python
context.metrics_engine.assert_metrics_emited(
    [MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [0, AnyFloat(), 0],
    [Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
    dimensions={"EventType": event_type.value},
    exception="RuntimeError",
    add_handler_dimensions=False,
)
```

### `assert_feature_metrics_emitted(feature, times=1)`

Asserts a feature metric (`MetricKey.COUNT` with `Feature` dimension) was emitted.

```python
context.metrics_engine.assert_feature_metrics_emitted(Feature.MEETING_CREATED)
```

### `assert_feature_metrics_not_emitted(feature)`

Asserts a feature metric was NOT emitted.

### `assert_metrics_not_emited(names, ...)`

Asserts metrics were NOT emitted. Same signature as `assert_metrics_emited` but inverted.

## StubMetrics (standalone, without context)

For CLI tests or code that creates its own metrics logger, instantiate `StubMetrics` directly:

```python
from tests.helpers import StubMetrics

stub = StubMetrics()
# ... run code that uses stub as its metrics logger ...
stub.assert_metrics_emited(
    [MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [0, AnyFloat(), 0],
    [Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
    dimensions={"EventType": event_type.value},
)
```

## AnyFloat

Use `AnyFloat()` for metric values where the exact number doesn't matter (e.g., timing):

```python
from tests.helpers import AnyFloat

# Matches any int or float
[0, AnyFloat(), 0]
```

## Fault prefix pattern

When a handler raises an exception, it emits a prefixed fault metric:

```python
MetricKey.FAULT.with_prefix(MetricKey.MEETING_NOT_OWNED)   # "MeetingNotOwned/Fault"
MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED)    # "MeetingNotOwned/Error"
```

NOTE: This reference documents the current metrics assertion API. If the monitoring system is being refactored, check `tests/helpers/monitoring.py` for the latest signatures.
