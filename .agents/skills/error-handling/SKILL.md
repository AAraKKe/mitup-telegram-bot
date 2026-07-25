---
name: error-handling
description: Exception hierarchy and error handler conventions. Auto-load when adding new exceptions, guard errors, or modifying error_handler.py.
user-invocable: false
---

# Error Handling

The bot uses a structured exception hierarchy combined with a centralized error handler. All exceptions are defined in `libs/core/mitup_bot/exceptions.py`; the error handler lives in `apps/bot/mitup_bot/handlers/error_handler.py`.

## Error flow

1. A handler raises an exception (usually via a guard).
2. `callback_with_metrics()` in the registry catches it and calls `error_handler.handler()`.
3. The error handler decides whether to suppress, handle specially, or emit fault metrics.
4. Metrics are flushed regardless of outcome (in the `finally` block of `callback_with_metrics()`).

Errors are **not** routed through PTB's built-in error handler — they are caught directly in the registry wrapper so that the handler's metrics context (dimensions, properties) is preserved.

## Exception categories

### Guard exceptions

Raised by functions in `guards.py` when handler inputs are invalid. These are the most common exceptions:

| Exception | Guard | Meaning |
|-----------|-------|---------|
| `UserNotFound` | `current_user()` | Telegram user not in the database |
| `MeetupNotFound` | `Meetup.by_id(must_exist=True)` | Meeting ID doesn't exist (raised directly in edit handlers, not from a guard) |
| `MalformedCallbackData` | `valid_callback_data()`, `valid_meeting_callback_data()` | Callback data missing required `id` |
| `MeetingGoneError` | `meeting()` | The addressed meeting does not resolve to a row |
| `MeetingNotOwnedError` | `meeting()` | The caller holds none of the access the requested profile lets through |
| `MeetingInactiveOwnerError` | `meeting()` | The owner addressed a meeting of theirs that is no longer active |
| `EffectiveUserNotSet` | `current_user()` | Telegram update has no `effective_user` |
| `EffectiveChatNotSet` | `chat()` | Telegram update has no `effective_chat` |
| `EffectiveMessageNotSet` | `message()` | Telegram update has no `effective_message` |
| `CallbackQueryNotSet` | `callback_query()` | Update has no callback query |

### Context exceptions

| Exception | Meaning |
|-----------|---------|
| `ContextPropertyNotSetError` | User data property (meeting ID, text) not found in context registry |
| `ContextPropertyConversionError` | Stored value can't be converted to expected type |
| `InvalidUserData` | `user_data` is `None` (should never happen in practice) |

### Telegram interaction exceptions

| Exception | Meaning |
|-----------|---------|
| `InactiveUserInteraction` | A blocked/deleted user interacted with the bot |
| `CallbackQueryTextTooLong` | Callback query answer text exceeds 200 chars |
| `NoMessageAvailable` | Cannot edit — neither `message_id` nor `inline_message_id` present |

### Registration exceptions

| Exception | Meaning |
|-----------|---------|
| `HandlerRegisteredError` | Duplicate `HandlerId` in the registry |
| `HandlerNotRegistered` | Referenced handler not found during conversation handler composition |
| `WrongCommandNameError` | Command handler function doesn't follow naming convention |

## The error handler

`error_handler.handler()` in `apps/bot/mitup_bot/handlers/error_handler.py` is the entry point for all caught exceptions.

### Suppressed errors

`SUPPRESSED_EXCEPTIONS` maps exception types to sets of known-harmless message strings; `should_ignore_error()` checks this mapping and suppresses silently:

```python
SUPPRESSED_EXCEPTIONS = {
    BadRequest: {"Message to edit not found"},
}
```

<note>
Two suppression mechanisms exist at different layers — use the correct one:

- **`SUPPRESSED_EXCEPTIONS`** (global, `error_handler.py`): for Telegram API errors that can arise anywhere and should be silently ignored across all handlers.
- **`EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`** (inline, `api_wrapper.py`): for errors specific to `edit_message` calls (e.g. "Message is not modified") — caught before they reach the global error handler.

Never add try/except blocks in handlers for either case.
</note>

### Inactive user handling

`InactiveUserInteraction` with `private=True` triggers `handle_inactive_user()`, which transitions the user's `status` from `MEMBER` to `LEFT` via `User.mark_inactive()` and emits `INACTIVE_USER_SET`. This happens when:
- A user has blocked the bot (raises `Forbidden`)
- A user's account is deleted (raises `BadRequest` with "not found")

The `private` flag distinguishes private chat errors (where we should mark inactive) from group chat errors (where we should not). The `TelegramApi` methods in `api_wrapper.py` raise `InactiveUserInteraction` with the appropriate `private` value.

### Meeting guard rejections

The three `MeetingAccessError` subclasses (see the table above) are answered by `handle_meeting_access_error()` and never reach the fault metrics. Each one stands for a screen, and the exception carries everything the screen needs — `meeting_id`, `action`, `lang`, and the back-navigation `keyboard` — so no DB round-trip happens here. `guards.meeting` renders nothing itself.

The reply shape comes from the update, not from the exception:

| Update | Reply |
|---|---|
| callback query | `edit_message` — the screen the button sits on is replaced |
| message | `send_message` — there is no message of ours to replace |
| inline query | `answer_inline_query` with `meeting.unavailable_inline_view` |

Acting on a meeting that is gone, inactive or somebody else's is what a stale button produces, not a code fault, so the branch emits `FAULT=0` — the interaction is counted as a completed one, exactly like a handler that ran to the end. `MeetingNotOwnedError` additionally logs a warning and emits `ERROR/MeetingNotOwned`; `MeetingGoneError` logs the warning only; `MeetingInactiveOwnerError` does neither, since offering an owner the reactivation prompt says nothing about their intent.

Delivery is best-effort, like every other render in this module: an exception raised while answering has no handler left above it and would reach `process_update` as a second, unhandled fault.

### Fault metrics

For all other (unexpected) errors:
1. A specific fault metric is emitted: `FAULT/<ErrorClassName>` (e.g., `FAULT/ValueError`)
2. A global `FAULT` metric is emitted for aggregate monitoring
3. Stack traces are attached to all loggers via `add_stack_trace()`
4. In `DEV` mode, the exception is logged with Rich formatting

## The `handle_edit_errors` context manager

`api_wrapper.py` provides `handle_edit_errors()` for safe message editing:

```python
async with handle_edit_errors(adapter=self.adapter):
    await self.adapter.bot.edit_message_text(...)
```

It handles two cases:
- **Message not modified** (content unchanged) — silently ignored via `EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`
- **Message not found** (deleted by user) — emits `MESSAGE_DELETED`

All `edit_message` calls in `TelegramApi` already use this. Do not add custom try/except blocks for these errors.

## Adding new exceptions

1. Define the exception in `libs/core/mitup_bot/exceptions.py`.
2. Include contextual data (user IDs, handler IDs, callback data) in the constructor — this aids debugging.
3. If the exception should be suppressed, add it to `SUPPRESSED_EXCEPTIONS` in the error handler.
4. If the exception needs special handling (like `InactiveUserInteraction`), add a branch in `error_handler.handler()`.
5. If guards raise the new exception, register affected handlers in `tests/bot/handlers/test_failure_modes.py` (see the `test-conventions` skill's `references/failure-modes.md`).
