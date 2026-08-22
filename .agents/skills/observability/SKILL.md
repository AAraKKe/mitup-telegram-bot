---
name: observability
description: The logging and metrics contract for mitup_bot — structlog event names, reason vocabularies, ambient binds, the EMF property allowlist, exactly-once Fault, the api_method rule, and adopt-or-retire for MetricKey. Auto-load before adding or editing ANY log line or metric emission, in any workspace member.
user-invocable: false
---

# Observability

Load this before writing a `log.*` call or a metric emission. The `monitoring` skill covers the EMF
*mechanism* (clients, backends, units, dimensions-vs-properties); this skill covers **what may go on
each plane and how it must be named**. Where they overlap, this skill states the contract and
`monitoring` states the API.

The rationale, the production incidents behind each rule, and the per-surface audit live in the
observability design doc (§2 is the normative contract). Do not reproduce it here — this file is the
working reference.

## The division of labour

**CloudWatch metrics answer "is something wrong". Logs Insights answers "what".**

| Plane | Role | Carries |
|---|---|---|
| structlog | the **narrative** | the ordered story of one invocation: decisions, mutations, refusals, outbound calls |
| EMF | a **receipt** | metric values, closed-enum dimensions, and ONE correlation key that indexes into the narrative |

<critical_rules>
An EMF record is an index entry, not a second copy of the story. Every EMF datapoint must have at
least one log line that explains it; no log line's content may be duplicated onto a record to make
it "queryable" — it already is, on the log plane.
</critical_rules>

## The structlog contract

<critical_rules>
  <rule>The `event` string is a **constant** — a literal or a module-level constant. Never an f-string, never `.format()`, never concatenation. Every variable fact is a named keyword field.</rule>
  <rule>A line must be actionable **read in isolation**. If understanding a line requires reading the previous one, the line is wrong.</rule>
  <rule>Ambient fields are bound once at the choke point and **never repeated** on individual lines. The bot binds `update_id`/`handler`/`handler_type`/`flow`/`tg_user_id`/`chat_id` (`handler_log_context`, `update_log_context`); events binds `flow`/`run_id` (`handle_maintainance`); web binds `request_id`. Passing one of those explicitly on a line is a defect, not redundancy.</rule>
  <rule>Guards bind what they **resolve** — `guards.meeting` binds `meeting_id` on its success return, so every later line names the meeting without passing it.</rule>
</critical_rules>

### Levels

| Level | Meaning |
|---|---|
| `debug` | High-frequency internals we accept losing in prod. No decisions, no mutations. |
| `info` | Narrative: a step happened, a decision was taken, a mutation landed, something was sent. Includes deliberate no-ops and benign skips, **with a `reason`**. |
| `warning` | A real anomaly the system handled: rejections, refusals, degraded output, swallowed failures, malformed input. |
| `error` | We could not do the thing and nobody downstream will fix it. Always `exc_info` plus `error_type`. |

Two corollaries. Prod runs INFO, so **a decision logged at DEBUG does not exist**. And a `warning`
must never be the only record of a normal outcome — pair every rejection warning with the
corresponding allow-path record, or the widget cannot tell "no incidents" from "no traffic".

### Picking an event name and a reason vocabulary

**One event name per decision, with a `reason=` enum for its variants.** Never mint a second event
name for a variant of a decision already named; never mint a name that collides in meaning with an
existing one across producers. The name says *what was decided*; `reason` says *which branch*;
`outcome` says *what happened*.

Three worked examples from this codebase:

- **`"Meeting datetime input rejected"`** (`handlers/meeting/edit/when/start.py`,
  `when/end.py`) — six call sites, one name, `reason` ∈ `invalid_time_value`,
  `wrong_time_format`, `wrong_datetime_format`, crossed with `field` ∈ `start`/`end`. One filter
  covers every rejection and `stats count() by reason, field` breaks it down for free, where a
  metric series per branch would answer neither question.
- **`"Hosts-only group join request gated"`** (`handlers/hosts_group/entry.py`) — one name for the
  gate decision, `reason` ∈ `active_supporter`, `not_a_supporter`, `unknown_telegram_user`, crossed
  with `outcome` ∈ `approved`, `declined`, `approve_failed`, `decline_failed`. The pair is the
  design: `reason` is why we decided, `outcome` is whether Telegram accepted it.
- **`"Rejected Patreon webhook, invalid or missing signature"`** (`web/patreon.py`) — one 403 to the
  caller, three operator answers, via the `SignatureVerdict` enum: `no_secret_registered`,
  `missing_signature_header`, `digest_mismatch`.

<critical_rules>
`reason` and `outcome` are always snake_case values from a **bounded set**, never a sentence and
never a free string. Back the set with a `StrEnum` whenever it has more than about three members —
`DraftRefusal`, `TagAnomaly`, `EntityDropReason`, `SignatureVerdict`, `WebhookDrift` are the
existing ones to copy.

Where a helper collapses several causes into one return value (a bare `bool`, a `None`), the helper
must **return or expose the reason** so its caller can bind it. A helper that answers `False` for
three different reasons destroys the only evidence that distinguishes them.
</critical_rules>

### Field names

Use the registry below; do not invent a synonym for a field that already exists.

| Field | Meaning |
|---|---|
| `tg_user_id` | Telegram user id |
| `user_id` | internal `User.db_id` |
| `chat_id`, `chat_type`, `update_id`, `update_type` | Telegram identities |
| `meeting_id`, `joined_link_id`, `subscription_id`, `message_db_id`, `broadcast_id`, `delivery_id`, `context_id` | domain ids |
| `run_id`, `request_id` | correlation keys |
| `reason` | snake_case machine value naming *why* |
| `outcome` | snake_case machine value naming *what happened* |
| `error_type` | fully-qualified exception class, `module.QualName` |
| `phase`, `committed` | where in the write lifecycle a failure happened, and whether the transaction landed |
| `previous_status`, `status`, `supporter_level`, `window_days` | state-transition and lifecycle facts |
| `api_method`, `duration_ms`, `status_code` | outbound-call facts |
| `stage`, `step`, `state`, `field`, `setting`, `list`, `check` | facets of a shared event name |
| `callback_data`, `command` | what the user pressed or typed, on the bot's handler lines only — see [The interaction-input carve-out](#the-interaction-input-carve-out) |

Order: ids first, then decision inputs, then `reason`/`outcome`.

One id space per key, never mixed. When a line needs a *second* chat or user that is not the
update's, give it a qualified name (`hosts_group_chat_id`) — rebinding `chat_id` to a different
meaning silently overrides the ambient value for that record.

<critical_rules>
Never bind a raw PTB `Update` or a config object — it renders as an unfilterable repr.
</critical_rules>

## What never reaches either plane

<critical_rules>
  <rule>**No user-supplied text.** Ids and bounded enum values only. Message text, meeting titles and descriptions, addresses, usernames, search queries, uploaded filenames, free-form names: log `*_len`, `has_*`, a rounded value or a presence flag instead. `retry_timezone_step` is the reference — it carries `address_length=len(address)` and never the address.</rule>
  <rule>**Deep-link payloads normalize through a closed vocabulary before logging.** A `/start` payload is client-forgeable and unbounded; map it onto the closed source enum first and log the enum value, never the raw payload.</rule>
  <rule>**Two fields are carved out of the rule above, and only two: `callback_data` and `command`.** Both are bounded by something outside our code, which is what admits them — read [The interaction-input carve-out](#the-interaction-input-carve-out) before copying the shape onto anything else.</rule>
  <rule>**No URLs, no headers, no tokens, no secrets, no signed state.** See the api_method rule below.</rule>
  <rule>**No rendered prose on a record.** A property may carry an id or a bounded enum value; never a sentence, never a list of them.</rule>
  <rule>**No tracebacks on a record.** structlog's `format_exc_info` is the single renderer. A `log.exception` beside the fault emission puts it on the log plane once.</rule>
</critical_rules>

### The interaction-input carve-out

Without the input, an operator reading a callback interaction cannot tell which button was pressed
and a command interaction cannot be told from any other. `handler_entry_fields`
(`apps/bot/mitup_bot/handlers/registry.py`) is the single producer of the two fields that answer
that, and they ride the bot's handler entry and exit lines only. What admits them is that each is
bounded by a contract we do not own:

- **`callback_data`** is the payload of the pressed button, capped at 64 bytes by the Telegram Bot
  API. On a handler that registered a pattern, the payload also had to satisfy that pattern to get
  there, so what lands is the bot's own callback vocabulary plus ids. The unrouted-callback fallback
  is the exception — nothing matched, so its exit line carries 64 bytes the client chose, and that
  payload is precisely the diagnostic for "why did this button route nowhere".
- **`command`** is the bare command, split off at the first whitespace. **Its arguments stay
  forbidden**: a `/start` deep-link payload or a search term is free user text and falls squarely
  under the rules above.

They ride the **exit** line because prod runs INFO — carried by the entry DEBUG line alone they do
not exist in production. Putting them on the line that already closes every invocation buys the
input for zero extra log volume; promoting the entry line instead would double every invocation's
line count for the same fact.

Nothing generalises from this beyond those two fields. A value earns a place here only by being
capped by an external contract *and* constrained to a vocabulary the bot itself minted.

### The api_method rule

<critical_rules>
Outbound telemetry records **`api_method` only** — never a request URL, never a header, never a
body. This binds the log plane, the metric plane, and every auto-instrumentation setting alike.
`api_method` is derived from the path segment *after* the `bot<token>/` prefix
(`libs/telegram/mitup_bot/request.py`) or passed in as a literal label.
</critical_rules>

The Telegram Bot API embeds the token in **every** URL
(`https://api.telegram.org/bot<TOKEN>/<method>`), so anything that records a request URL publishes
the bot token. Two concrete instances of this leak class, both real:

1. **A dimension derived from a request URL.** The ADOT httpx auto-instrumentation built the
   Application Signals `RemoteOperation` **metric dimension** from the request URL. The production
   token sat in a CloudWatch metric dimension for roughly seven days — readable with view-only
   access, non-redactable, retained 15 months. New auto-instrumentation is enabled by **allowlist,
   never left on by denylist**: an instrumentation that must be disabled to be safe is an allowlist
   decision.
2. **A `ValidationError` chained from a credential-endpoint response.** A pydantic parse failure on
   a token-endpoint body renders the rejected value in its message, and the chained `__cause__`
   carries it into the traceback. Report the **field paths and error types**, and **suppress the
   cause** (`raise ... from None`) for credential endpoints — `CREDENTIAL_API_METHODS` in
   `libs/patreon/mitup_bot/patreon/client.py` is the pattern.

Defence in depth, not a substitute for either: `logging_config` carries a redaction processor that
scrubs any value of the bot-token shape, and a test asserts the token substring reaches neither
plane. Both exist because the rule was broken once; do not treat them as permission to relax it.

Corollary: **the method is a log field, never a dimension.** An `ApiMethod` dimension would mint one
billed series per method *and* empty the dimensionless widget that charts them. Per-method breakdown
is `stats avg(duration_ms), pct(duration_ms, 99) by api_method, outcome` over the line.

## The EMF contract

A record MAY contain exactly four things, and nothing else.

1. **Metric values.** Static constant names from `MetricKey` or a literal. `with_prefix` takes a
   literal or an enum member only — never a value derived from runtime data.
2. **Dimensions.** Closed enums only (`Feature`, `EventType`). Dimension names *and values* are
   alarm contract: renaming an enum member is an infra change even when the metric name is
   byte-identical.
3. **The correlation key** — exactly one per record, mandatory, no exceptions. Bot: `update_id`.
   Events: `run_id`. Web and webhooks: `request_id`. Pool: whichever is ambient. A record with no
   correlation key is a metric that can page and then offer no way into the story.
4. **A closed property allowlist**, and nothing outside it:

| Record shape | Permitted properties |
|---|---|
| bot | `update_id`, `handler`, `handler_type`, and on fault records only `error_type` |
| events | `run_id` (plus the `EventType` dimension) |
| web / webhook | `request_id` |
| db pool | the ambient correlation key |
| background jobs | `run_id` |

The background-jobs shape is the card-refresh worker's (`libs/telegram/mitup_bot/card_refresh.py`).
Its correlation key is the `run_id` of the reporting window, passed **per emit** because the client
is process-lived, and written by the **reporter alone**: a property is last-writer-wins across the
flush window, so an id stamped by one job would be reported as describing every other job in the
window. The jobs bind that same id on their log lines, which is what the pivot walks. For the same
reason `origin_update_id` — the update whose commit queued a job — stays on the log plane; it may
ride a record only where a flush window covers exactly one job.

Properties use the **same lowercase snake_case vocabulary as structlog fields**, so one Insights
query reads both record shapes. Dimensions keep CloudWatch-facing CamelCase — a different namespace
with a different contract.

<critical_rules>
Adding a property is a design decision needing a named metric-plane consumer, not a convenience.
Anything that varies **within a flush window** may not be a property at all: `put_metric` appends
while `set_property` is last-writer-wins, so a varying property is reported as if it described every
emission in the window. See the `monitoring` skill for the mechanism and
`broadcast/recording.py::emit_delivery_outcomes` for the reference implementation.
</critical_rules>

### Fault is emitted exactly once per unit of work

<critical_rules>
`MetricKey.FAULT` is written **exactly once per unit of work — not at most once** — by the framework
only. The unit is the invocation in the bot (the registry's `finally` in `callback_with_metrics`),
the run in events (`handle_maintainance`), and one job in the card-refresh worker
(`RefreshQueue.run_next`). No handler, no helper, no `error_handler.handler`, no post-commit drain
may emit it.
</critical_rules>

*Exactly* once, because `MitupCriticalFaultRate` uses `Fault`'s **SampleCount as its request
denominator** and both ECS `deployment_alarms` blocks read it. An exit path that returns without
emitting does not report "no fault" — it removes the invocation from the denominator and inflates
the rate, and the benign classifications (a suppressed Telegram error, a lost conversation context)
arrive in a burst during exactly the rolling deploy that is reading the alarm. So the error handler
**returns** a `FaultOutcome` classification and the registry emits the one sample.

The refresh worker is the one writer whose flush window holds more than one unit: a reporting window
covers every job it ran, so `Fault` serializes there as one sample per job. That is deliberate — it
puts background jobs in the same population the fault-rate alarm reads, so a queue timing out every
job forever pages like anything else that fails, and the 0s keep its denominator honest. The array
is also why the worker's triage is the `Background job failed` line and not a `filter Fault = 1`:
Logs Insights flattens repeated values to `Fault.0`/`Fault.1`. Nothing else may take this shape —
one unit per window everywhere else.

A fact that is not the invocation outcome gets its own metric name — `PostCommitApiFault` is the one
for a queued delivery that failed after commit.

The one addition: `registry.process_update_error` samples a failure that reached PTB's error plane
with no wrapped callback owning it, and skips the update entirely when its trace already carries a
fault.

### A metric is a continuous time series

<critical_rules>
A value earns an EMF series only if it is emitted on **every** occurrence of its carrier cadence —
per request, per run, per stats tick — so its widget draws an unbroken line. An **event-conditional
counter**, one that fires only when the thing happens and has no meaningful zero in between, is a
log line with a `reason`, not a series.
</critical_rules>

Operational observables qualify inherently: latency, faults and request counts are emitted by the
wrapper that owns every invocation, so a gap in them is real traffic information. State gauges
qualify because the stats job recomputes them every tick whatever the answer is. A departure, a
refusal, a webhook rejection does not — its series is mostly absent, which CloudWatch renders as
missing data rather than zero, so no alarm can read it and no widget can chart a rate from it.

The **0-baseline** is the deliberate device that converts a conditional counter into a continuous
one: emit `0` on the path that clears the condition and `1` on the path that trips it, and the
series becomes alarmable. `PatreonWebhookForbidden` and `PatreonWebhookFault` are the reference
implementations. Reach for it only when something actually reads the series — otherwise the value
belongs on a log line, where `stats count() by reason` costs nothing per distinct value.

One consequence worth stating: when several code paths produce the same domain event, they share
**one** event name and separate themselves with `reason`, rather than each minting a counter.
`User status changed` is the reference — five paths reach `User.mark_inactive`, each passes its
`InactiveReason`, and one query splits every way a user can leave.

### Adopt or retire

<critical_rules>
A new `MetricKey` may not merge without **either** a Terraform reader — a dashboard widget, an alarm
or a saved query — **or** an explicit note in the key's docstring that it is diagnostic-only and
deliberately unread. State which one in the MR description.
</critical_rules>

Ten keys shipped in ten days with neither, including a security counter counting into the void. A
name being bounded is necessary, not sufficient: a series still needs a **named consumer** before it
is worth minting. A varying facet with no consumer belongs on an existing series instead — the
pattern for a user-input error is the `Feature`-dimensioned `ERROR` series with a `reason` property.

Member docstrings in `MetricKey` go **after** the member they describe (PEP 258 attribute-docstring
position), so the adopt-or-retire note lands on the right key.

### Process-level samples

Samples raised by process-lived infrastructure (the DB pool) ride the invocation's flush window via
`current_metrics_client()` and `emit_aggregate` — see **"Process-level samples inside an
invocation"** in the `monitoring` skill for the mechanism and the reason `emit()` is wrong there.
`set_global_property` on a process-lived client is banned: `MetricsLogger.flush` copies properties
into the fresh context and `EmfBackend` never clears them, so one call pins that value onto every
subsequent record until the task restarts. Those clients pass the correlation key **per emit**.

## The pivot (the acceptance test)

If these steps do not work end to end, the contract is not implemented, however many lines were
added.

1. An alarm fires; the alert carries the alarm description and a console deep link.
2. The **index** query returns one row per fault with `update_id` as a first-class column.
3. Copy an `update_id` into the **trace** query.
4. Read the full ordered story, one metric row at the end.
5. Aggregate questions are answered by a Logs Insights `stats` over structlog fields — **not** over
   metric dimensions.

## Comment discipline

<critical_rules>
Comments in this domain state **current facts only**. Never narrate repo history ("used to emit…",
"after !574…"), and never write diff-context prose — in particular, never a comment justifying a
line that is *absent*. If a rule explains why a field is missing, that belongs in this skill or in
the MR description, not in a comment beside the gap.
</critical_rules>

A comment that states a live constraint is right and wanted: *why* a property is constant for the
window, *why* a value is a length rather than the value. Test docstrings carry the "why this
assertion exists" load.

## Testing log lines and metrics

<critical_rules>
  <rule>Test the shared emission **mechanism once**, plus one variant per distinct branch with a different value. Never repeat an assertion for a shared line at every call site that reaches it — that tests the call graph, not the code.</rule>
  <rule>Asserting a field is **absent** must be done against a line that **exists**. Assert the line was emitted, then assert the field is not on it. A bare "this string does not appear in the logs" passes vacuously when the line was never produced, when it was renamed, and in five translated locales.</rule>
  <rule>Assert on structured fields, never on rendered message text. Fields ride as `LogRecord` attributes via the suite's pipeline — read them off the record (`log_record(caplog, event)`), not out of `caplog.text`.</rule>
  <rule>Pin the event name, the level and the `reason` value in the test, spelled literally rather than imported from the module under test, so a reworded name fails the assertion instead of travelling silently into it.</rule>
</critical_rules>

The cross-cutting contract tests live in `tests/observability/`; per-flow trace tests live beside
the handler they cover. `MetricAssertions.assert_emitted(..., times=N)` is how exactly-once is
pinned — `times=1`, not merely "emitted".
