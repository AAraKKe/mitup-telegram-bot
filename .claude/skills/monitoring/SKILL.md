---
name: monitoring
description: CloudWatch EMF metrics conventions. Auto-load when adding metrics, MetricKey/Feature enums, or emit_metric/put_feature_metric calls.
user-invocable: false
---

# Monitoring & Metrics

The bot uses [AWS Embedded Metrics Format (EMF)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html) for zero-cost CloudWatch metric emission. The monitoring layer lives in `mitup_bot/monitoring/`.

## Architecture

### Metrics engine

`MitupMetricsEngine` is the central coordinator. It manages multiple `MitupMetricsLogger` instances, one per unique `Dimensionality`. Loggers with identical dimensions share a single EMF log line — this is a **cost optimization** (CloudWatch charges per log line, not per metric within a line).

The engine is created per-request in `MitupContext.from_update()` and flushed after every handler invocation by `callback_with_metrics()` in the registry.

### Dimensionality

`Dimensionality` is an immutable, hashable bag of key-value dimension pairs. The engine uses it as a cache key — calling `get_logger(Dimensionality(Handler="Show", HandlerType="Callback"))` twice returns the same logger instance. `NULL_DIMENSIONALITY` is the singleton for dimensionless metrics.

### Sinks

Three output backends exist, selected via `MetricsConfig.environment`:

| `MetricsEnv` | Backend | When used |
|--------------|---------|-----------|
| `CLOUDWATCH` | AWS CloudWatch (default EMF) | Production |
| `STDOUT` | Stdout (local EMF environment) | CI, automated testing |
| `RICH` | `RichConsoleSink` — pretty-printed JSON via Rich | Local development |

The sink is configured once globally by `configure_metrics()` in `app.py`. Never select sinks conditionally in handler code.

## Emitting metrics from handlers

All handler metrics go through `MitupContext` methods. Never instantiate loggers or call EMF directly.

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
- `emit_global` — also emits a dimensionless copy for aggregate dashboards.

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

`Feature` members track **user-facing feature usage**. Add a new member when introducing a new user action that should be tracked independently (e.g., a new way to set timezone, a new meeting action). See existing members in `monitoring/metric_keys.py` for the pattern.

## Outside handler contexts

For code that runs outside the request cycle (lambdas, CLI scripts), use `MitupMetricsEngine` directly with `auto_flush()` or `async_auto_flush()`:

```python
engine = MitupMetricsEngine(logger_provider=lambda ep: MitupMetricsLogger(ep))
with engine.auto_flush() as metrics:
    metrics.put_metric(MetricKey.INACTIVE_USERS_DELETED, count, Unit.COUNT)
```

`BotAdapter` (used in lambdas/CLI) does **not** emit metrics — its `emit_metric()` and `with_time_metric()` are no-ops. If metrics are needed from a lambda, use the engine directly.
