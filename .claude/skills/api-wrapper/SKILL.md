---
name: api-wrapper
description: Telegram API wrapper conventions. Auto-load when using TelegramApiWrapper, BotAdapter, ContextOrBotAdapter, or sending/editing messages.
user-invocable: false
---

# Telegram API Wrapper

The API abstraction layer lives in `mitup_bot/api_wrapper.py`. It decouples handler logic from raw Telegram Bot API calls and provides a consistent interface for both handler and non-handler contexts.

## Architecture

### `TelegramApiWrapper` (Protocol)

Defines the interface that all API implementations must satisfy. Handlers and views interact only with this protocol.

### `TelegramApi` (Implementation)

The concrete implementation. Requires an `adapter` (conforming to `ContextOrBotAdapter`) to be set before use. All outbound calls go through the adapter's `bot` property.

### `ContextOrBotAdapter` (Protocol)

Defined in `protocols.py`. Requires `bot`, `with_time_metric()`, `emit_metric()`, and `flush_metrics()`. Two conforming types exist:

| Adapter | Context | Metrics |
|---------|---------|---------|
| `MitupContext` | Handler execution (full PTB context) | Full metrics emission |
| `BotAdapter` | Lambda/CLI (bare `ExtBot`) | Metrics via provided `MetricsClient` |

## Usage in handlers

Always access the API through `context.api`:

```python
await context.api.send_message(update, view)
await context.api.edit_message(update, view)
await context.api.answer_callback_query(update, text, show_alert=False)
```

Never call `context.bot.*` directly — this bypasses error handling, timing metrics, and the abstraction layer.

## Usage outside handlers (lambdas, CLI)

Use `BotAdapter` to wrap a bare `ExtBot`:

```python
from mitup_bot.api_wrapper import build_api

api = build_api(bot)  # bot is an ExtBot instance
await api.send_message_to_user(user, view)
```

Or construct manually:

```python
from mitup_bot.api_wrapper import TelegramApi, BotAdapter

adapter = BotAdapter(bot, metrics_client)
api = TelegramApi()
api.adapter = adapter
```

`BotAdapter` requires a `MetricsClient` to emit metrics. For convenience, `build_api(bare_ext_bot)` defaults to `NullBackend` when you don't need metrics (see the `monitoring` reference skill).

## Key methods

| Method | Purpose |
|--------|---------|
| `send_message()` | Send a new message to the chat from the update |
| `send_message_to_user()` | Send a message to a user by their `tg_user_id` |
| `send_messages_to_users()` | Batch send to multiple users with error handling |
| `edit_message()` | Edit an existing message (handles inline messages too) |
| `answer_inline_query()` | Respond to an inline query with results (`cache_time` defaults to 60s; pass `0` for dynamic results) |
| `answer_callback_query()` | Acknowledge a button press |
| `update_single_meeting_message()` | Update one stored meeting message |
| `update_meeting_messages()` | Broadcast meeting state to all stored messages |
| `notify_users_promoted_from_waiting_list()` | Notify users promoted from waiting list |

## Error handling patterns

### Inactive user detection

`send_message_to_user()` catches `Forbidden` (bot blocked) and `BadRequest` with "not found" (deleted account), raising `InactiveUserInteraction(user_id, private=True)`. The error handler then marks the user inactive in the database.

`send_messages_to_users()` handles this internally — inactive users are marked directly without propagating exceptions, so batch sends don't fail on individual user errors.

### Edit error suppression

All edit operations use the `handle_edit_errors()` context manager, which handles:

- **"Message is not modified"** — silently ignored (content unchanged, no-op)
- **"Message to edit not found" / "Message_id_invalid"** — message deleted by user; the `Message` DB record is deleted and `MESSAGE_DELETED` metric is emitted

These error patterns are defined as compiled regexes (`MESSAGE_NOT_FOUND_ERROR_PATTERNS`, `EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`). Do not add custom try/except blocks for these cases — use the existing mechanism.

### Meeting message broadcast

`update_meeting_messages()` iterates all `Message` records linked to a `Meetup` and updates each via `update_single_meeting_message()`. It supports:

- `skip_current` — skip the message from the current update (useful when the current message is already being edited differently)
- `was_deleted` — show "meeting has been deleted" text
- `has_finished` — show "meeting has finished" text with no buttons
- `current_message` — update this message first for faster perceived responsiveness

## Timing metrics

All Telegram API calls are wrapped with `context.with_time_metric("TelegramApi")`, emitting `TelegramApiTime` metrics in milliseconds. This is handled automatically by the API methods — do not add redundant timing around `context.api.*` calls.
