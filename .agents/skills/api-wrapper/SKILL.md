---
name: api-wrapper
description: Telegram API wrapper conventions. Auto-load when using TelegramApiWrapper, BotAdapter, ContextOrBotAdapter, or sending/editing messages.
user-invocable: false
---

# Telegram API Wrapper

The API abstraction layer lives in `libs/telegram/mitup_bot/api_wrapper.py`. It decouples handler logic from raw Telegram Bot API calls and provides a consistent interface for both handler and non-handler contexts.

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

## The commit-aware outbox (write mode)

Under the write lifecycle — `db.begin_write(api)` directly, or `@with_session(write=True)` in handlers (see the `database` skill) — the api runs in **capture mode**: every `api.*` call enqueues a `QueuedApiCall` onto an `ApiOutbox` instead of executing, and the lifecycle drains the queue in order after the transaction commits. Callers keep their linear style — only execution time moves.

Capture rules baked into `TelegramApi`:

- Queue entries carry **plain data snapshotted at enqueue time** (chat ids, message ids, rendered view content). `update_meeting_messages` renders each stored message into a `MeetingMessageEdit` payload at enqueue; the button persistence (`message.buttons.keyboard = ...`) happens then too, inside the transaction. Nothing reads ORM objects or the session during the drain.
- Argument validation (missing chat/query, view resolution) also happens at enqueue, inside the transaction, so programming errors fail early and abort.
- `send_messages_to_users` rejects `on_success`/`on_error` callbacks under capture (they would mutate ORM objects after commit and be lost) — callers needing them must use `immediate`.
- `context.api.immediate.X(...)` executes right away, inside the open transaction; its failure aborts the transaction. Keep it rare and greppable.

### Post-commit error semantics (decided in #188)

By the time the drain runs, the DB state is committed and correct — a failing queued call is a **partial rendering failure**, never a reason to touch the DB or stop unrelated deliveries. `execute_queued` therefore isolates each call:

- `InactiveUserInteraction` (blocked bot / deleted account) → the tg user id is recorded on the outbox; the lifecycle's reconcile transaction marks the user inactive (emitting `INACTIVE_USER_SET`). The drain continues.
- BadRequest "message is not modified" → success (two updates can render identical content under concurrency).
- Any other per-call exception → logged plus `Fault<ErrorType>` and aggregate `Fault` metrics via the adapter (mirroring the error handler's shape), and the drain continues — the remaining entries are independent deliveries to other chats.
- `NetworkError` (excluding its `BadRequest` subclass) → systemic: every remaining call would fail the same way, so the drain stops and the error surfaces to the global error handler. The reconcile fix-ups collected so far are still applied.

### The reconcile transaction

Fan-out execution can discover DB fix-ups: messages deleted by users (`dead_message_ids`) and unreachable users (`inactive_tg_user_ids`). The write lifecycle applies both in one short follow-up transaction after the drain — never interleave ad-hoc API-then-DB writes in a caller; record onto the outbox instead. The reconcile owns the dead-message cleanup for every caller: `update_single_meeting_message`/`update_meeting_messages` take no session, and in immediate mode a dead message only emits its metric (the stale row is picked up by the next write-mode fan-out).

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

A non-handler broadcast (mutate state, then fan out) wraps each critical section in `async with db.begin_write(api)` to get the same capture → commit → drain → reconcile lifecycle as write-mode handlers — see the `database` skill.

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

All edit operations route through the shared suppression logic (`handle_edit_errors()` for update-based edits, the `MeetingMessageEdit` executor for meeting-message fan-outs), which handles:

- **"Message is not modified"** — silently ignored (content unchanged, no-op)
- **"Message to edit not found" / "Message_id_invalid"** — message deleted by user; `MESSAGE_DELETED` is emitted and the stale `Message` DB record is removed via the outbox reconcile transaction (write mode only — immediate mode just emits the metric)
- **`Forbidden` on a meeting-message fan-out edit** — the chat is unreachable (user blocked the bot, deactivated their account, or the bot lost group access); same dead-message treatment: `MESSAGE_DELETED` plus the reconcile removal. `handle_edit_errors()` does not catch `Forbidden` — update-based edits answer a live interaction, where it cannot occur.

The suppressed patterns are compiled regexes defined inside `libs/telegram/mitup_bot/api_wrapper.py` (grep for `_ERROR_PATTERNS` / `_ERRORS_TO_IGNORE_PATTERNS` to see the current list — the exact names and set of patterns change as Telegram's error strings evolve). Do not add custom try/except blocks for these cases — extend the regex list in that module instead.

### Meeting message broadcast

`update_meeting_messages()` iterates all `Message` records linked to a `Meetup` and updates each via `update_single_meeting_message()`. It supports:

- `skip_current` — skip the message from the current update (useful when the current message is already being edited differently)
- `was_deleted` — show "meeting has been deleted" text
- `has_finished` — show "meeting has finished" text with no buttons
- `current_message` — update this message first for faster perceived responsiveness

## Timing metrics

All Telegram API calls are wrapped with `context.with_time_metric("TelegramApi")`, emitting `TelegramApiTime` metrics in milliseconds. This is handled automatically by the API methods — do not add redundant timing around `context.api.*` calls.
