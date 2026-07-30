---
name: monitoring
description: CloudWatch EMF metrics conventions. Auto-load when adding metrics, MetricKey/Feature enums, or emit_metric/put_feature_metric calls.
user-invocable: false
---

# Monitoring & Metrics

The bot uses [AWS Embedded Metrics Format (EMF)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html) for zero-cost CloudWatch metric emission. The monitoring layer lives in `libs/monitoring/mitup_bot/monitoring/`.

This skill is the EMF **mechanism** — clients, backends, units, and the dimensions-vs-properties
cost rule. The `observability` skill states the **contract** the two planes share: which facts may
ride a record at all, the correlation-key requirement, the property allowlist, the adopt-or-retire
rule for a new `MetricKey`, and the structlog side none of this covers. Load it too whenever the
work adds a metric or a log line.

## Architecture

### MetricsClient

`MetricsClient` is the central metrics API. It accepts a `MetricsBackend` and optional `base_dimensions`, accumulates `MetricRecord` instances internally, and delegates emission to the backend.

In handler contexts, `MitupContext` wraps `MetricsClient` and provides convenience methods (`emit_metric`, `put_feature_metric`). The client is created per-request in `MitupContext.from_update()` and flushed after every handler invocation by `callback_with_metrics()` in the registry.

### MetricsBackend

`MetricsBackend` is a protocol with two implementations:

| Backend | When used |
|---------|-----------|
| `EmfBackend` | Production — delegates to `aws_embedded_metrics` for real CloudWatch emission |
| `NullBackend` | Tests — silent no-op; records are readable from `MetricsClient.records` only when the client is built with `record_history=True` (see `tests/helpers/monitoring.py:make_test_metrics_client()`) |

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

## Properties must be constant for the flush window

A property describes the whole EMF document, not one emission. `EmfBackend` keys its loggers by
dimensionality, so every emission sharing a dimension set lands in the same document: metric values
**accumulate into an array** while `set_property` is **last-writer-wins**. A property whose value
varies per emission therefore survives only for the emission that wrote it last, and is reported as
if it described all of them.

<critical_rules>
An EMF property may only carry a fact that holds for the entire flush window (a run id, a broadcast
id, the handler identity of the one invocation being flushed). Anything that varies per emission —
an outcome, an attempt number, a target id — belongs on a metric series of its own or on a structlog
line. `emit_delivery_outcomes` in `apps/events/mitup_bot/events/broadcast/recording.py` is the
reference implementation: per-delivery status rides as one-hot 0/1 metrics, only the run-constant
`broadcast_id` rides as a property.
</critical_rules>

Note how wide a flush window can be: the bot flushes once per handler invocation, but the
recurrent-events service flushes once per **run**, so a sweep touching hundreds of users produces
hundreds of samples under a single property set.

## Narrative belongs to structlog, never to a record

<critical_rules>
An EMF record is an index entry: the metric, its dimensions, and the few short keys needed to find
the matching log lines (`update_id`, `run_id`, `Handler`, `error_type`). Everything that *reads as
prose* stays on the log plane — a snapshot of the triggering update, a rendered sentence per failed
item, a stack trace. All three are written once per emission or once per logger, so they are
repeated across the whole flush window, and none of them can be alarmed on.

The traceback in particular has a single renderer: structlog's `format_exc_info`. A `log.exception`
/ `log.error(exc_info=...)` line beside the fault emission puts it in the log plane once; nothing
copies it onto the record. The bot fault path (`error_handler.handler`) and the events run wrapper
(`handle_maintainance`) are the reference implementations.
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

# A shared-surface counter, emitted without the handler identity because the series is about the
# surface rather than the callback that reached it. Dimensions stay a closed enum — see below.
context.emit_metric(MetricKey.STALE_MEETING_MESSAGE, 0, include_handler_properties=False)
```

Key parameters:
- `include_handler_properties` (default `True`) — attaches `Handler` and `HandlerType` as EMF **properties** (not dimensions).

<critical_rules>
The canonical application of the [Dimensions vs. properties](#dimensions-vs-properties) rule above: handler identity (`Handler`/`HandlerType`) rides as EMF **properties**, never as dimensions, so only the dimensionless series is emitted per metric name. Do **not** reintroduce a `Handler`/`HandlerType` dimension or a duplicate "global" emission of a handler metric. See [issue #205](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/205).
</critical_rules>

### `context.put_feature_metric()`

Convenience wrapper that adds a `Feature` dimension. Use it to track feature-level usage:

```python
context.put_feature_metric(Feature.JOIN_MEETING)
context.put_feature_metric(Feature.SET_TIMEZONE, name=MetricKey.ERROR, properties={"reason": "invalid_google_geocode_response"})
```

## Outbound calls

Every outbound HTTP round-trip — Telegram, Patreon, Google Maps — is recorded once by
`outbound_call` in `libs/monitoring/mitup_bot/monitoring/outbound.py`: one structlog line plus a
`<Edge>Time`/`<Edge>Fault` pair, at the layer that makes one HTTP request. Do not add timing around
`context.api.*`, around a `PatreonClient` method, or around a `googlemaps` call — a wrapper
operation is zero or many round-trips, and timing it produces an unlabelled array nothing can
attribute.

```python
with outbound_call(PATREON_EDGE, api_method, timeout_errors=(httpx.TimeoutException,)) as call:
    response = await self._client.request(method, url, **kwargs)
    call.status_code = response.status_code
```

Three rules bind every edge:

- **Never a URL, never a header, never a request body.** The Telegram Bot API embeds the token in
  every URL, and a URL published as a metric dimension once put the production token into
  CloudWatch for a week, unredactable for 15 months. `api_method` is derived by matching the
  `bot<token>/` prefix (`libs/telegram/mitup_bot/request.py`) or passed in as a literal label; a
  test asserts the token substring reaches neither plane.
- **The method is a log field, never a dimension.** Per-method breakdown is
  `stats avg(duration_ms), pct(duration_ms, 99) by api_method, outcome` over the line. An
  `ApiMethod` dimension would mint one billed series per method and empty the dimensionless widget.
- **`<Edge>Fault` counts what the peer failed to answer** — a raised call, or a 5xx. A 4xx is the
  peer answering (a throttle, a rejected edit, a blocked user) and rides on the line's
  `status_code`.

The samples ride on whichever `MetricsClient` the invocation published via `bound_metrics_client`
(the registry for the bot, `handle_maintainance` for events), so they join that flush window and
inherit its correlation key instead of carrying properties of their own. A caller holding the
client — `timezone_api`, which has the context in hand — passes it explicitly instead.

## Automatic handler metrics

`callback_with_metrics()` in the registry automatically emits these for **every handler invocation** — do not duplicate them manually:

| Metric | Description |
|--------|-------------|
| `Time` | Handler latency in milliseconds (dimensionless) |
| `Fault` | `1` on exception, `0` on success (dimensionless) |
| `DbConnectionsLeaked` | Count of unreturned DB connections (should always be 0) |

Each is emitted as a single **dimensionless** series. The handler identity (`Handler`, `HandlerType`) is attached as EMF **properties** — set automatically via `context.prepare_handler_metrics()` — so per-handler drill-down happens in CloudWatch Logs Insights, not via a billed dimension.

<critical_rules>
`Fault` has a single writer, and writes **exactly once — not at most once**. It is the outcome of one invocation, emitted once per logger per flush window by the wrapper that owns the invocation (`callback_with_metrics`, `handle_maintainance`) and by nothing else — not a handler, not a helper, not `error_handler.handler`, not the post-commit outbox drain.

The one addition to that list covers the invocations that never started: `registry.process_update_error`, registered on PTB via `add_error_handler`, samples a failure that reached PTB's error plane without any wrapped callback owning it. When the update's trace (`update_trace.UPDATE_TRACE`) already carries a fault it skips the update entirely — no sample and no line — so a re-raise from an invocation that already closed itself is recorded once, by the invocation.

*Single* writer, because EMF **appends** repeated values under one metric name: a second writer serialises `"Fault": [1, 0]`, Logs Insights flattens the array to `Fault.0`/`Fault.1` so the `filter Fault = 1` triage queries stop matching, and the fault-rate alarm (Average + SampleCount, wired into the ECS rollback bakes) reads half the value on twice the samples.

*Exactly* once, because that same alarm uses `Fault`'s **SampleCount as its request denominator**. An exit path that returns without emitting does not report "no fault" — it removes the invocation from the denominator, inflating the fault rate. The classifications that end an interaction benignly (a suppressed Telegram error, an inactive user, a pending deletion, a meeting-guard rejection, a lost conversation context) are the ones most likely to arrive in a burst during a rolling deploy, which is exactly when a bake is reading the alarm. So the error handler **returns** its classification (`FaultOutcome`) to `callback_with_metrics`, which emits the one sample from its `finally`.

A fact that is not the invocation outcome gets its own metric name — `PostCommitApiFault` is the one for a queued delivery that failed after commit.
</critical_rules>

<critical_rules>
Metric **names** are static constants. Never mint one from a runtime value (`MetricKey.FAULT.with_prefix(type(exc).__name__)`): every distinct class creates its own separately-billed CloudWatch series, forever, and none of them is on a widget or in an alarm. `with_prefix` is for a **bounded** prefix — a literal known at author time (`TelegramApiTime`), or a value the code constrains to a closed set before using it (`<lang>/ActiveUsers`, filtered against `SUPPORTED_LANGUAGES`). A DB column is not a closed set on its own.

A name being bounded is necessary, not sufficient: a series still needs a **named consumer** — a widget or an alarm — before it is worth minting. A varying facet with no consumer belongs on an existing series instead. The pattern for a user-input error is the `Feature`-dimensioned `ERROR` series (`context.put_feature_metric(Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_time_format"})`): `Feature` is bounded and already auto-discovered by the dashboard, and `reason` names the branch without opening a series. The fault path applies the same rule with `error_type`.
</critical_rules>

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

## Process-level samples inside an invocation

A sample raised by a shared resource rather than by the work in hand — the connection pool is the
one such source — is attributed the same way an outbound call is: it rides
`current_metrics_client()`, joining the invocation's flush window and inheriting its correlation
key, and falls back to a process-scoped client outside any invocation. `db.record_pool_sample` is
the reference implementation.

<critical_rules>
Such a sample must be written with `MetricsClient.emit_aggregate()`, never `emit()`. The series is
process-wide and its alarm reads it dimensionless, so the dimensions must not depend on which
invocation happened to be on the stack — `emit_aggregate` drops the client's `base_dimensions` and
re-attaches them as EMF properties, keeping one series while the attribution rides the record.
Routing such a sample through `emit()` re-dimensions it per invocation (`EventType` in the events
runner), which mints a series per value and empties the one the alarm watches.
</critical_rules>

<note>
`BotAdapter` delegates metrics to the `MetricsClient` provided at construction. When a real backend (e.g., `EmfBackend`) is used, metrics are emitted normally. For convenience, `build_api(bare_ext_bot)` defaults to `NullBackend` when metrics are not needed (see the `api-wrapper` skill).
</note>
