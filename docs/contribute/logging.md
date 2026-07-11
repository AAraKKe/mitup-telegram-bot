---
icon: material/text-box-search-outline
---

# Logging

Mitup uses [structlog](https://www.structlog.org/) for structured logging. All log output flows through a single pipeline defined in [`libs/core/mitup_bot/logging_config.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/core/mitup_bot/logging_config.py), which configures both structlog-native loggers and stdlib/third-party loggers (httpx, python-telegram-bot, SQLAlchemy) through the same renderer.

## Dev vs prod output

The renderer is selected by the active environment.

In `Env.DEV`, logs are human-readable colorized output from `structlog.dev.ConsoleRenderer`. In any other environment, logs are emitted as JSON (`structlog.processors.JSONRenderer`) for ingestion by CloudWatch.

Both renderers share the same processor chain: contextvars merge, log level, logger name, ISO-UTC timestamp, and exception/stack info.

## How to log

Get a logger at module level with `structlog.get_logger(__name__)`:

```python
import structlog

log = structlog.get_logger(__name__)
```

The `__name__` argument sets the `logger` field on every line, which lets you identify the source module and tune per-namespace log levels without touching global config.

Log with an event string and structured keyword arguments:

```python
log.info("Meeting reminder sent", meeting_id=meeting.id, user_id=user.id)
log.warning("Reminder skipped: meeting already started", meeting_id=meeting.id)
```

Do not interpolate variable values into the event string:

```python
# Don't do this:
log.info(f"Meeting {meeting.id} reminder sent to {user.id}")

# Do this instead:
log.info("Meeting reminder sent", meeting_id=meeting.id, user_id=user.id)
```

Structured fields stay queryable in CloudWatch Logs Insights. F-string interpolation buries the data in the message text where it can't be filtered or aggregated.

## Request context via contextvars

You do not pass a logger through call signatures. Request fields are bound once per entry point and injected automatically into every downstream log line by the `merge_contextvars` processor.

Use `structlog.contextvars.bound_contextvars(...)` as a context manager. The binding clears automatically on exit, so fields never leak into the next request handled by the same worker.

```python
with structlog.contextvars.bound_contextvars(meeting_id=meeting.id, run_id=run_id):
    # every log.* call inside here carries meeting_id and run_id automatically
    await send_reminders(meeting)
```

## Reserved keys

Every log line carries a small set of reserved keys so CloudWatch queries filter and group on structured fields instead of parsing the event string. The keys fall into three layers by how long each one lives.

The bot service, the recurrent-events runner, and each Lambda call `configure_logging(env, component, level)` once before any logging happens, then bind their own invocation context. One-off CLI commands currently sit outside this pipeline: the legacy Rails migration tool applies its own plain-stdlib log setup, and the other operator commands configure no logging at all.

### Layer 1: `component`

`component` names the process that produced the line. A processor stamps it for the whole lifetime of the process, so it survives the asyncio task and thread boundaries that reset contextvars. One of:

* `bot`: the ECS bot service, covering both PTB handlers and the FastAPI web layer.
* `events`: the ECS recurrent-events runner.
* `lambda`: every AWS Lambda function.
* `cli`: reserved for one-off operator commands. Nothing emits it yet; new operator commands that log through the pipeline should pass `Component.CLI`.

### Layer 2: `flow`

`flow` names the business unit handling one invocation. It is bound through contextvars for the lifetime of that invocation and clears on exit. Its value depends on the component:

| Component | `flow` value |
|---|---|
| `bot` | the `HandlerId` subclass, e.g. `edit_meeting` or `commands` |
| `bot` (Patreon web) | the existing Patreon flow name |
| `events` | the `EventType` value, kept alongside the `event_type` key |
| `lambda` | `migrations`, `alarm_action`, or `migrate_from_rails` |

`handler` and `handler_type` stay bound on bot lines as the fine-grained drill-down beneath `flow`.

### Layer 3: correlation and identity keys

These pin a line to a single request, run, or user. Each is bound only when it applies.

| Key | Meaning |
|---|---|
| `update_id` | the Telegram update being processed, also an EMF global property |
| `run_id` | one recurrent-event run |
| `aws_request_id` | one Lambda invocation |
| `request_id` | one inbound HTTP request |
| `tg_user_id` | a Telegram user id |
| `user_id` | the internal DB primary key, never a Telegram id |
| `chat_id` | a Telegram chat id |

Because `update_id` is also a global property in the EMF metrics payload (`MitupContext`), a CloudWatch metric alarm can cross-link to the exact log lines from the same request.

Event strings stay human prose. Filtering and aggregation read the reserved keys above, never the message text.

## `MitupContext.log`

`MitupContext.log` is a convenience accessor that returns a structlog logger. It is optional sugar for code that already holds a `MitupContext`; standalone modules should use `structlog.get_logger(__name__)` directly instead.

## Library log levels

`configure_logging` tunes noisy third-party loggers:

* `httpx` is set to `WARNING` (suppresses per-request INFO lines).
* `telegram.ext.ExtBot` is set to `DEBUG` in `Env.DEV` and `WARNING` elsewhere.
