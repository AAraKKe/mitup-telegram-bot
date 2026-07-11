---
name: monitoring
description: CloudWatch EMF metrics conventions. Auto-load when adding metrics, MetricKey/Feature enums, or emit_metric/put_feature_metric calls.
user-invocable: false
---

# Monitoring & Metrics

The bot uses [AWS Embedded Metrics Format (EMF)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html) for zero-cost CloudWatch metric emission. The monitoring layer lives in `libs/monitoring/mitup_bot/monitoring/`.

## Architecture

### MetricsClient

`MetricsClient` is the central metrics API. It accepts a `MetricsBackend` and optional `base_dimensions`, accumulates `MetricRecord` instances internally, and delegates emission to the backend.

In handler contexts, `MitupContext` wraps `MetricsClient` and provides convenience methods (`emit_metric`, `put_feature_metric`, `with_time_metric`). The client is created per-request in `MitupContext.from_update()` and flushed after every handler invocation by `callback_with_metrics()` in the registry.

### MetricsBackend

`MetricsBackend` is a protocol with two implementations:

| Backend | When used |
|---------|-----------|
| `EmfBackend` | Production — delegates to `aws_embedded_metrics` for real CloudWatch emission |
| `NullBackend` | Tests — silent no-op; records are captured in `MetricsClient._records` only when the client is built with `record_history=True` (see `tests/helpers/monitoring.py:make_test_metrics_client()`) |

The backend is configured once globally by `configure_emf_backend()` in `app.py`. Never select backends conditionally in handler code.

### MetricRecord

`MetricRecord` is a frozen dataclass that captures a single metric emission:

- `name: str` — the metric name (typically a `MetricKey` value)
- `value: float` — the metric value
- `unit: MetricUnit` — one of `COUNT`, `MILLISECONDS`, `BYTES`, `SECONDS`, `PERCENT`, `NONE`
- `dimensions: frozenset[tuple[str, str]]` — immutable dimension pairs
- `properties: dict[str, Any]` — searchable EMF properties (not dimensions)

### MetricUnit

Custom `MetricUnit` enum (in `monitoring/units.py`) replaces the old `aws_embedded_metrics.unit.Unit`:

- `MetricUnit.COUNT`, `MetricUnit.MILLISECONDS`, `MetricUnit.BYTES`, `MetricUnit.SECONDS`, `MetricUnit.PERCENT`, `MetricUnit.NONE`

<critical_rules>
Always import `MetricUnit` from `mitup_bot.monitoring`, never from `aws_embedded_metrics`.
</critical_rules>

## Dimensions vs. properties

This is the rule that governs every emission path in this codebase — handler metrics, feature metrics, and outside-handler client metrics alike.

CloudWatch **dimensions** are reserved for **bounded, intentional facets** — ones where every value is a deliberately created, separately-billed metric series (`Feature`, `EventType`). Each distinct dimension-value combination is one CloudWatch series (billed per series-month), so the dimension key set is a cost commitment, not a place to stash context.

**Identity-like or high-cardinality facets** — `Handler`, `HandlerType`, user IDs, callback data, meeting IDs — must ride as EMF **properties** instead. Properties travel inside the EMF log line in the `MitupEcsService` log group: zero metric cost, and still fully queryable per-record via CloudWatch Logs Insights (e.g. `filter Fault = 1 | stats sum(Fault) by Handler`).

<critical_rules>
When adding a new metric, **default to properties**. Promoting a facet to a dimension is a deliberate cost decision that you must justify by its bounded cardinality — never a convenience for getting a value onto a graph. Dropping an unbounded base dimension and re-attaching it as a property (as handler identity does here, and as an outside-handler client's global copy does when it strips its base dimensions) is this same rule applied, not a special case.
</critical_rules>

## Emitting metrics from handlers

<critical_rules>
All handler metrics go through `MitupContext` methods. Never instantiate clients or call EMF directly.
</critical_rules>

### `context.emit_metric()`

The primary method. Handles dimensions and properties:

```python
# Most common: emit a metric carrying the handler identity as EMF properties (auto-included)
context.emit_metric(MetricKey.ERROR, 1)

# Custom dimensions, no handler identity attached
context.emit_metric("ApiCallCount", 1, dimensions={"Service": "Google"}, include_handler_properties=False)
```

Key parameters:
- `include_handler_properties` (default `True`) — attaches `Handler` and `HandlerType` as EMF **properties** (not dimensions).
- `include_update_properties` (default `True`) — attaches Telegram update metadata (user ID, callback data, message text) as EMF properties (searchable but not dimensioned).

<critical_rules>
The canonical application of the [Dimensions vs. properties](#dimensions-vs-properties) rule above: handler identity (`Handler`/`HandlerType`) rides as EMF **properties**, never as dimensions, so only the dimensionless series is emitted per metric name. Do **not** reintroduce a `Handler`/`HandlerType` dimension or a duplicate "global" emission of a handler metric. See [issue #205](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/205).
</critical_rules>

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
| `Time` | Handler latency in milliseconds (dimensionless) |
| `Fault` | `1` on exception, `0` on success (dimensionless) |
| `DbConnectionsLeaked` | Count of unreturned DB connections (should always be 0) |

Each is emitted as a single **dimensionless** series. The handler identity (`Handler`, `HandlerType`) is attached as EMF **properties** — set automatically via `context.prepare_handler_metrics()` — so per-handler drill-down happens in CloudWatch Logs Insights, not via a billed dimension. The dimensionless `Fault` series is what the infra CloudWatch fault alarms and the ECS deploy gate read, and it is emitted exactly once per invocation — never emit a duplicate copy.

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

Pass `emit_global=True` to `MetricsClient.emit()` when a base-dimensioned client also needs an aggregate series to alarm on. It emits a second, dimensionless copy of the metric that drops `base_dimensions` but keeps them as EMF **properties** — so one alarm can watch across every base-dimension value while Logs Insights still breaks the aggregate down. `MitupContext.emit_metric()` has no `emit_global` parameter: handler metrics are dimensionless by construction (handler identity always rides as properties, never as a dimension), so there's no base-dimensioned series to collapse. Example: the recurrent-events service emits per-`EventType` `Fault`/`Time` plus a dimensionless global copy (`EventType` demoted to a property) so a single `Mitup/Events` alarm catches any failing event type.

```python
client = MetricsClient(EmfBackend(), base_dimensions={"EventType": event_type.value})
client.emit(MetricKey.FAULT, 1, MetricUnit.COUNT, emit_global=True)
```

<note>
`BotAdapter` delegates metrics to the `MetricsClient` provided at construction. When a real backend (e.g., `EmfBackend`) is used, metrics are emitted normally. For convenience, `build_api(bare_ext_bot)` defaults to `NullBackend` when metrics are not needed (see the `api-wrapper` skill).
</note>
