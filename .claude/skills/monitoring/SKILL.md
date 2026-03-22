---
name: monitoring
description: CloudWatch EMF metrics conventions. Auto-load when adding metrics, MetricKey/Feature enums, or emit_metric/put_feature_metric calls.
user-invocable: false
---

# Monitoring & Metrics

The bot uses [AWS Embedded Metrics Format (EMF)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html) for zero-cost CloudWatch metric emission. The monitoring layer lives in `mitup_bot/monitoring/`.

## Architecture

### MetricsClient

`MetricsClient` is the central metrics API. It accepts a `MetricsBackend` and optional `base_dimensions`, accumulates `MetricRecord` instances internally, and delegates emission to the backend.

In handler contexts, `MitupContext` wraps `MetricsClient` and provides convenience methods (`emit_metric`, `put_feature_metric`, `with_time_metric`). The client is created per-request in `MitupContext.from_update()` and flushed after every handler invocation by `callback_with_metrics()` in the registry.

### MetricsBackend

`MetricsBackend` is a protocol with two implementations:

| Backend | When used |
|---------|-----------|
| `EmfBackend` | Production — delegates to `aws_embedded_metrics` for real CloudWatch emission |
| `NullBackend` | Tests — silent no-op; records are still captured in `MetricsClient._records` |

The backend is configured once globally by `configure_emf_backend()` in `app.py`. Never select backends conditionally in handler code.

### MetricRecord

`MetricRecord` is a frozen dataclass that captures a single metric emission:

- `name: str` — the metric name (typically a `MetricKey` value)
- `value: float` — the metric value
- `unit: MetricUnit` — one of `COUNT`, `MILLISECONDS`, `BYTES`, `SECONDS`, `NONE`
- `dimensions: frozenset[tuple[str, str]]` — immutable dimension pairs
- `properties: dict[str, Any]` — searchable EMF properties (not dimensions)

### MetricUnit

Custom `MetricUnit` enum (in `monitoring/units.py`) replaces the old `aws_embedded_metrics.unit.Unit`:

- `MetricUnit.COUNT`, `MetricUnit.MILLISECONDS`, `MetricUnit.BYTES`, `MetricUnit.SECONDS`, `MetricUnit.NONE`

<critical_rules>
Always import `MetricUnit` from `mitup_bot.monitoring`, never from `aws_embedded_metrics`.
</critical_rules>

## Emitting metrics from handlers

<critical_rules>
All handler metrics go through `MitupContext` methods. Never instantiate clients or call EMF directly.
</critical_rules>

### `context.emit_metric()`

The primary method. Handles dimensions, properties, and optional global aggregation:

```python
# Most common: emit a metric with handler dimensions (auto-included)
context.emit_metric(MetricKey.ERROR, 1)

# Custom dimensions, no handler context
context.emit_metric("ApiCallCount", 1, dimensions={"Service": "Google"}, include_handler_dimensions=False)

# Emit with handler dims AND a dimensionless copy for cross-handler aggregation
context.emit_metric(MetricKey.FAULT, 0, emit_global=True)
```

Key parameters:
- `include_handler_dimensions` (default `True`) — adds `Handler` and `HandlerType` dimensions automatically.
- `include_update_properties` (default `True`) — attaches Telegram update metadata (user ID, callback data, message text) as EMF properties (searchable but not dimensioned).
- `emit_global` — additionally emits a dimensionless copy of the metric (no handler or custom dimensions) for cross-handler aggregate dashboards.

### `context.put_feature_metric()`

Convenience wrapper that adds a `Feature` dimension. Use it to track feature-level usage:

```python
context.put_feature_metric(Feature.JOIN_MEETING)
context.put_feature_metric(Feature.TIMEZONE_WITH_LOCATION, name=MetricKey.ERROR)
```

### `context.with_time_metric()`

Context manager that measures elapsed time and emits a `<prefix>Time` metric in milliseconds:

```python
with context.with_time_metric("TelegramApi"):
    await context.bot.send_message(...)
```

All Telegram API calls in `TelegramApi` already use this — do not add redundant timing around `context.api.*` calls.

## Automatic handler metrics

`callback_with_metrics()` in the registry automatically emits these for **every handler invocation** — do not duplicate them manually:

| Metric | Description |
|--------|-------------|
| `Time` | Handler latency in milliseconds (with handler dims + global) |
| `Fault` | `1` on exception, `0` on success (with handler dims + global) |
| `DbConnectionsLeaked` | Count of unreturned DB connections (should always be 0) |

Handler dimensions (`Handler`, `HandlerType`) are set automatically via `context.prepare_handler_metrics()`.

## Adding a new `MetricKey`

Standard metric names live in `MetricKey` (a `CamelCaseStrEnum` in `monitoring/metric_keys.py`). The enum auto-converts `SNAKE_CASE` names to `CamelCase` values.

Add a new member when:
- The metric represents a **distinct observable** (not just a different value of an existing metric).
- Multiple call sites will emit the same metric name.

If only one handler emits a metric, a plain string is acceptable — but prefer `MetricKey` for discoverability.

## Adding a new `Feature`

`Feature` members track **user-facing feature usage**. Add a new member when introducing a new user action that should be tracked independently (e.g., a new way to set timezone, a new meeting action). Follow the naming pattern in `monitoring/metric_keys.py`.

## Outside handler contexts

For code that runs outside the request cycle (lambdas, CLI scripts), create a `MetricsClient` directly with the appropriate backend:

```python
from mitup_bot.monitoring import MetricsClient, EmfBackend, MetricKey, MetricUnit

client = MetricsClient(backend, base_dimensions={"EventType": event_type.value})
client.emit(MetricKey.INACTIVE_USERS_DELETED, count, MetricUnit.COUNT)
await client.flush()
```

Use `NullBackend()` in tests and `EmfBackend(...)` in production. The `base_dimensions` are merged into every emission automatically.

<note>
`BotAdapter` delegates metrics to the `MetricsClient` provided at construction. When a real backend (e.g., `EmfBackend`) is used, metrics are emitted normally. For convenience, `build_api(bare_ext_bot)` defaults to `NullBackend` when metrics are not needed (see the `api-wrapper` skill).
</note>
