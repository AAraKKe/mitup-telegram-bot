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

Every log line carries a small set of reserved keys so CloudWatch queries filter and group on structured fields instead of parsing the event string. Two are always present: `component` names the process the line came from (the bot service, the recurrent-events runner, a Lambda), and `flow` names the business unit handling one invocation, such as a handler on the bot or an event type on the runner. Correlation keys like `update_id`, `run_id`, or `request_id` join them when they apply, pinning a line to a single request, run, or user.

When you add log lines inside an existing entry point, the binding is already done for you. The practical rule is to add your own keyword arguments and never rebind or overwrite the reserved keys. Because `update_id` is also a global property in the EMF metrics payload (`MitupContext`), a CloudWatch metric alarm can cross-link to the exact log lines from the same request.

## `MitupContext.log`

`MitupContext.log` is a convenience accessor that returns a structlog logger. It is optional sugar for code that already holds a `MitupContext`; standalone modules should use `structlog.get_logger(__name__)` directly instead.

## Library log levels

`configure_logging` tunes noisy third-party loggers:

* `httpx` is set to `WARNING` (suppresses per-request INFO lines).
* `telegram.ext.ExtBot` is set to `DEBUG` in `Env.DEV` and `WARNING` elsewhere.
