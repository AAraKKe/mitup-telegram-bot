---
icon: material/text-box-search-outline
---

# Logging

Mitup uses [structlog](https://www.structlog.org/) for structured logging. All log output flows through a single pipeline defined in [`mitup_bot/logging_config.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/logging_config.py), which configures both structlog-native loggers and stdlib/third-party loggers (httpx, python-telegram-bot, SQLAlchemy) through the same renderer.

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

## Entry points and what they bind

Every process entry point must call `configure_logging(env, level)` once before any logging happens, then bind its own invocation context.

| Entry point | Binding site | Fields bound |
|---|---|---|
| Bot handlers | `handlers/registry.py` `callback_with_metrics` | `handler`, `handler_type`, `update_id`, `user_id`*, `chat_id`* |
| Recurrent events (CLI) | `cli/commands/recurrent_events.py` `launch_event` | `event_type`, `run_id` |
| Lambda handlers | each handler function | `lambda`, `aws_request_id`* |

\* bound only when present on the update or Lambda context.

The `update_id` field also appears as a global property in the EMF metrics payload (`MitupContext`). This lets a CloudWatch metric alarm cross-link to the exact log lines from the same request.

## `MitupContext.log`

`MitupContext.log` is a convenience accessor that returns a structlog logger. It is optional sugar for code that already holds a `MitupContext`; standalone modules should use `structlog.get_logger(__name__)` directly instead.

## Library log levels

`configure_logging` tunes noisy third-party loggers:

* `httpx` is set to `WARNING` (suppresses per-request INFO lines).
* `telegram.ext.ExtBot` is set to `DEBUG` in `Env.DEV` and `WARNING` elsewhere.
