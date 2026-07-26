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
- `send_messages_to_users` rejects its result callbacks under capture (they would mutate ORM objects after commit and be lost) — callers needing them must use `immediate`.
- `context.api.immediate.X(...)` executes right away, inside the open transaction; its failure aborts the transaction. Keep it rare and greppable.

### Post-commit error semantics

By the time the drain runs, the DB state is committed and correct — a failing queued call is a **partial rendering failure**, never a reason to touch the DB, stop unrelated deliveries, or tell the user their action failed. **`execute_queued` never raises**: nothing it hits reaches the global error handler, so the user keeps the success screen the handler already rendered (at worst one of their cards stays stale). Each call is isolated:

- `InactiveUserInteraction` (blocked bot / deleted account) → the tg user id is recorded on the outbox; the lifecycle's reconcile transaction marks the user inactive (emitting `INACTIVE_USER_SET`). The drain continues.
- BadRequest "message is not modified" → success (two updates can render identical content under concurrency).
- Any other per-call exception, network ones included → logged with the queued call name and its payload, plus a `PostCommitApiFault` metric via the adapter, and the drain continues — the remaining entries are independent deliveries to other chats. It never emits the aggregate `Fault`: that is the invocation outcome, the handler is still on its way to completing normally, and a second writer on the shared EMF logger turns the datapoint into an array (see the `monitoring` skill).
- The drain logs its outcome once (`queued` / `sent` / `failed` / `abandoned`).

#### The retry policy

Telegram has no idempotency key, so a failure that may already have been applied can only be retried when a second delivery is harmless. Each `QueuedApiCall` therefore carries an `idempotent` flag, set at **enqueue** time (the drain sees an opaque closure and cannot tell an edit from a send): edits, markup clears and query answers are idempotent, every send is not. `_invoke_queued_with_retry` retries a `NetworkError` up to `QUEUED_CALL_ATTEMPTS` times, waiting `QUEUED_CALL_RETRY_BACKOFF_SECONDS` doubled per retry, when either:

- the failure proves the request never left this process — its `__cause__` is one of `CONNECT_FAILURE_CAUSES` (PTB wraps httpx errors and keeps the original as `__cause__`); or
- the call is idempotent.

A read timeout on a send is therefore **never** retried: Telegram may have delivered it. `BadRequest` (a `NetworkError` subclass) is a permanent answer and never retried.

#### The circuit breaker

With short timeouts and a widely-shared meeting, a dead network would otherwise cost one timeout per remaining delivery inside the handler. After `DRAIN_NETWORK_FAILURE_LIMIT` **consecutive** network failures the drain abandons the rest of the queue and logs the abandoned call names. Any non-network outcome (including an unreachable user or an unchanged message — both reached Telegram) resets the counter. The reconcile fix-ups collected before the abort are still applied.

New api methods that enqueue must pass `idempotent=True` only when repeating the call cannot deliver anything twice; the default is the safe answer.

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

It classifies every recipient and runs **exactly one** callback per user: `on_success` when the message landed, `on_unreachable` when the user blocked the bot or no longer exists, `on_error` for anything else. A caller that only passes `on_success` therefore learns nothing about the other two outcomes — pass `on_unreachable` whenever an undeliverable notice must still drive a decision (e.g. a cleanup job that has to reach a terminal state rather than re-nominating the same row on every run).

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
