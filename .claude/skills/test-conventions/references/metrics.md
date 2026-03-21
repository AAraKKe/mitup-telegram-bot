# Metrics Assertions

## Overview

Metrics are tested via `MetricAssertions` from `tests.helpers.monitoring`. It wraps a `MetricsClient` backed by `NullBackend` and inspects the accumulated `MetricRecord` list for assertions.

Two fixtures are available globally (defined in `tests/conftest.py`):

- `metrics_client` — a `MetricsClient(NullBackend())` instance injected into contexts.
- `metrics` — a `MetricAssertions(metrics_client)` wrapper for assertions.

## Fixture wiring

### Handler tests

The `metrics_client` is automatically wired into `context` and `handler_context` fixtures:

```python
async def test_something(
    context: StubMitupContext,
    metrics: MetricAssertions,
    ...
):
    await some_handler(update, context)
    await context.flush_metrics()

    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.JOIN_MEETING)})
```

After calling handlers, **always call `await context.flush_metrics()`** before asserting — this flushes the backend and ensures all records are captured.

### CLI tests

CLI commands receive `MetricsClient` directly. Override the `metrics_client` fixture locally when you need `base_dimensions`:

```python
from mitup_bot.monitoring import MetricKey, MetricsClient, NullBackend
from tests.helpers.monitoring import MetricAssertions

@pytest.fixture
def metrics_client() -> MetricsClient:
    return MetricsClient(NullBackend(), base_dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value})

@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)

async def test_cli_command(mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi):
    await some_cli_command.run(api, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
```

For CLI tests, call **`await metrics_client.flush()`** directly (there is no context wrapper).

## MetricAssertions API

### `assert_emitted(**kwargs)`

Asserts a metric was emitted the expected number of times.

```python
metrics.assert_emitted(
    name=MetricKey.FAULT,          # Required — str or MetricKey
    value=1,                       # None = skip value check
    unit=MetricUnit.MILLISECONDS,  # None = skip unit check
    dimensions={"Feature": "X"},   # None = skip; subset match by default
    dimensions_exact=False,        # True = require exact dimension match (no extra dims allowed)
    properties={"key": "val"},     # None = skip; subset match by default
    properties_exact=False,        # True = require exact property match
    exception=UserNotFound,        # type[Exception] or str; checks record's exception properties
    times=1,                       # Expected emission count
)
```

**Dimension matching:** By default, `dimensions={"Feature": "X"}` matches records that have _at least_ that dimension (subset match). Use `dimensions_exact=True` to require the record has _exactly_ those dimensions and no others.

**Value flexibility:** Pass `value=None` to skip value checking entirely. This replaces the old `AnyFloat()` pattern for non-deterministic values like timing. However, `AnyFloat()` is still available via `tests.helpers` for cases where you need a float-matching sentinel in other assertions.

### `assert_not_emitted(**kwargs)`

Convenience wrapper — same parameters as `assert_emitted` but asserts `times=0`:

```python
metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
```

## Important: MetricUnit.MILLISECONDS for TIME metrics

When asserting `MetricKey.TIME`, you **must** explicitly pass `unit=MetricUnit.MILLISECONDS`. The default unit is `MetricUnit.COUNT`, which causes a silent mismatch:

```python
from mitup_bot.monitoring import MetricUnit

# Correct
metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS)

# Wrong — will silently not match because unit defaults to COUNT
metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat())
```

## Exception matching

Pass `exception=` to match the exception recorded in a metric's properties:

```python
# By class — resolves to fully qualified name for matching
metrics.assert_emitted(name=MetricKey.FAULT, value=1, exception=UserNotFound)

# By string — matches against the error_type property
metrics.assert_emitted(name=MetricKey.FAULT, value=1, exception="RuntimeError")
```

## Fault prefix pattern

When a handler raises an exception, it emits a prefixed fault metric:

```python
MetricKey.FAULT.with_prefix(MetricKey.MEETING_NOT_OWNED)   # "MeetingNotOwned/Fault"
MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED)    # "MeetingNotOwned/Error"
```

## Common handler metrics pattern

Every handler invocation automatically emits `Fault`, `Time`, and `DbConnectionsLeaked`. Assert all three when testing handler-level metrics:

```python
metrics.assert_emitted(name=MetricKey.FAULT, value=0)
metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS)
metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0)
```

For handlers that raise exceptions:

```python
metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)  # handler dims + global
metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("UserNotFound"), value=1)
metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)
```

## Feature metrics

Assert feature usage metrics emitted via `context.put_feature_metric()`:

```python
metrics.assert_emitted(
    name=MetricKey.COUNT,
    value=1,
    dimensions={"Feature": str(Feature.JOIN_MEETING)},
)

# Negative assertion
metrics.assert_not_emitted(
    name=MetricKey.COUNT,
    dimensions={"Feature": str(Feature.NEW_LANDING)},
)
```
