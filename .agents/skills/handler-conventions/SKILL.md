---
name: handler-conventions
description: Telegram handler conventions for mitup_bot. Auto-load when writing, editing, or reviewing handlers, registration methods, conversation handlers, or HandlerId enums.
user-invocable: false
---

# Handler Conventions

A handler is an async function decorated with a `HandlersRegistry` registration method. It receives a PTB `Update` and `MitupContext` and is invoked when a matching Telegram event arrives.

## Registration methods

| Method | Purpose |
|--------|---------|
| `register_command` | Registers a `/command` handler |
| `register_message` | Handles incoming text or media messages |
| `register_callback_query` | Handles button presses (inline keyboard callbacks) |
| `register_conversation_handler` | Multi-step conversation with states and fallbacks |
| `register_inline_handler` | Handles inline queries |
| `register_chosen_inline_result` | Handles the result a user picked from an answered inline query (`pattern` matches `result_id`) |

Every registration method requires a `handler_id` argument — a `HandlerId` enum member that uniquely identifies the handler.

## Database session

Decorate the handler with `@with_session` from `mitup_bot.db`. This injects an `AsyncSession` as the **first positional argument**; all session I/O and the DB-touching guards are awaited:

```python
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from sqlmodel.ext.asyncio.session import AsyncSession


@HandlersRegistry.register_callback_query(handler_id=MyHandlerId.SHOW)
@with_session
async def show(session: AsyncSession, update: Update, context: MitupContext):
    user = await guards.current_user(update, session)
    ...
```

**Broadcast ⇒ write mode:** any handler that mutates state and then fans out over Telegram — it takes the per-meeting row lock (`for_update=True`), calls `update_meeting_messages`, or notifies users — MUST use `@with_session(write=True)`. Under write mode: the decorator commits the transaction — releasing the per-meeting row lock — before executing the handler's `context.api` calls, which are queued as plain-data snapshots. Don't add defensive pre-send flushes, and reach for `context.api.immediate.X(...)` only when a call genuinely must run before commit. See the `database` and `api-wrapper` skills for the full lifecycle and error semantics.

## Conversation handlers

`register_conversation_handler` composes previously registered handlers into a state machine:

- `entry_points_handler_names` — handler IDs that trigger the start of the conversation.
- `states` — `dict[Enum, list[HandlerId]]` mapping state keys to handlers.
- `fallbacks` — handlers used when no state matches the incoming update.

**Entry points must be registered before the conversation.** The registry looks up each handler ID at registration time — if a handler referenced in `entry_points_handler_names` hasn't been registered yet, `HandlerNotRegistered` is raised. Verify that `handlers/__init__.py` imports the entry-point module before the module that calls `register_conversation_handler`.

**Circular imports.** If module A needs an enum from module B and vice versa, extract shared enums into standalone files (e.g., `command_enums.py`, `enums.py`) or use a local import inside the function body. See `command_enums.py` (extracted `CommandsId`) and `commands.py` (local import of `ConversationMeetingState`) for examples.

**Fallbacks and exit handling:**

- **Optional flows** (meeting creation, editing): call `context.store_on_exit(ContextId.<X>, message, cancel_callback)` in the entry handler and set `fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT]`. `MESSAGE_WITHOUT_TEXT` shows an interruption view and keeps the user in state so they can cancel explicitly.
- **Mandatory onboarding flows** (registration, required setup the user cannot skip): use a dedicated fallback handler (filter `~filters.TEXT | filters.COMMAND`, registered with `bindable=False`) that informs the user and returns the same state.

<critical_rules>
  <rule>Text handlers inside a conversation's `states` dict MUST use `filters.TEXT & ~filters.COMMAND` so commands fall through to the fallback.</rule>
  <rule>Wrong-input catch-all handlers (where both TEXT and ~TEXT variants call the same fallback) SHOULD use a single handler with `~filters.COMMAND` instead of two separate handlers. This still excludes commands so they fall through to the conversation fallback. Place the catch-all last in the state handler list.</rule>
</critical_rules>

## Filters

Handlers accept PTB `BaseFilter` instances to narrow which updates they process. Custom filters are in `personal_filters.py` (e.g., `PositiveNumberFilter`).

<critical_rules>
  <rule>Filters run synchronously during handler matching and MUST NOT touch the database — the async engine cannot be driven from sync code. Any DB-dependent routing belongs in the handler layer.</rule>
</critical_rules>

### DB-dependent routing (`/start`)

`/start` routing is the reference pattern: the re-onboarding conversation binds in `REGISTRATION_HANDLERS_GROUP` (-1, processed before group 0). Its entry checks `guards.member_user` — members fall through silently (plain `return ConversationHandler.END`) to the group-0 `/start` handler, while onboarding handlers claim their updates by raising `ApplicationHandlerStop(next_state)` (via the `claim_update` decorator in `registration_process/utils.py`, applied OUTSIDE `with_session` so the transaction commits before the raise). `callback_with_metrics` re-raises `ApplicationHandlerStop` so PTB stops the remaining groups and applies the carried state.

## Handler structure

Each feature submodule typically contains:

| File | Purpose |
|------|---------|
| `enums.py` | `HandlerId` subclass with members identifying each handler in the module |
| `entry.py` | Entry-point callback (usually the conversation entry or main action) |
| Other files | Supporting handlers, views, and utilities for the feature |

## Callback data

All button interactions use `CallbackData` — a Pydantic model defined in `libs/core/mitup_bot/callback_data.py`. Predefined instances for the whole bot live in `libs/telegram/mitup_bot/utils/callbacks.py`. When adding a new handler that needs a button action, add its callback instance there.

### Formats

| Class | Format | Use when |
|-------|--------|----------|
| `CallbackData` | `{action};{entity}:{id}` | Standard action on an entity |
| `DateCallbackData` | `{action};{entity}:{id};date:{YYYY-MM-DD}` | Action involves a date (e.g., setting a meeting date) |
| `MeetingCallbackData` | `{action};{entity}:{id}:{meeting_id}` | Action targets a subject (id) within a specific meeting |

### Defining a new callback

```python
# In libs/telegram/mitup_bot/utils/callbacks.py
from mitup_bot.callback_data import CallbackData

MY_ACTION = CallbackData(action="my_action", entity="my_entity")
```

Use `.with_id(id)` at call sites to attach a specific record ID:

```python
cb.MY_ACTION.with_id(meeting.db_id)
```

### Naming conventions

- **Destructive flows** — the `DELETE_<X>` / `CONFIRM_<X>` / `DECLINE_<X>` three-step pattern for irreversible actions is owned by the `views` skill. When you define callbacks for a destructive flow, follow that pattern.
- Action and entity names use `snake_case`.
- Keep action and entity strings short — callback data is limited to **64 bytes** total when encoded.

### Cancel buttons in conversations

<critical_rules>
  <rule>Any button that cancels an action inside a `ConversationHandler` MUST use `action="cancel"` in its `CallbackData` definition. This ensures the global stale cancel handler (`stale_cancel.py`) automatically handles the button if it is tapped outside an active conversation. Using any other action (e.g., `"decline"`) is a bug.</rule>
</critical_rules>

### Using callbacks in handlers

Pass the predefined instance directly to `ButtonConfig` or as a filter pattern for `register_callback_query`:

```python
from mitup_bot.utils import callbacks as cb

# As a filter
@HandlersRegistry.register_callback_query(handler_id=MyId.SHOW, callback_data=cb.SHOW_MEETING)

# In a ButtonConfig
ButtonConfig(text=ButtonMessages.SHOW.get(lang=lang), callback_data=cb.SHOW_MEETING.with_id(meeting_id))
```

## Handler function naming

Name handler functions after their registration type and action:

| Registration | Naming pattern | Example |
|---|---|---|
| `register_callback_query` | `callback_query_<action>` | `callback_query_toggle_lock` |
| `register_message` | `<action>_message_handler` | `duration_text_message_handler` |
| `register_command` | `command_<name>` | `command_start` |

<critical_rules>
  <rule>Callback query handlers MUST be named `callback_query_<action>`. Never omit the `callback_query_` prefix (e.g. NOT `callback_date_time_entry`, but `callback_query_date_time_entry`).</rule>
</critical_rules>

## Adding a new handler — checklist

1. Define a `HandlerId` member in the appropriate `enums.py` (or create a new submodule).
2. Write the handler function with the `@HandlersRegistry.register_*` decorator. Follow the naming convention above.
3. Add `@with_session` if database access is needed.
4. Register the handler in `tests/bot/handlers/test_failure_modes.py` if it calls any `guards.*` function (e.g. `guards.current_user`, `guards.meeting`, `guards.valid_callback_data`, `guards.valid_meeting_callback_data`).
5. Create a dedicated test file at `tests/bot/handlers/<package>/test_<module>.py`.
6. Import the handler module in `apps/bot/mitup_bot/handlers/__init__.py`.

## Removing a handler — checklist

When deleting a handler entirely:

1. Remove its `HandlerId` member from `enums.py`.
2. Delete the handler function and any callbacks/views it exclusively owns.
3. **Remove its `Context` entries from `tests/bot/handlers/test_failure_modes.py`** — every guard call registered there becomes stale and will cause test failures if left behind.
4. Remove its import from `apps/bot/mitup_bot/handlers/__init__.py` if the whole module is gone.

## Shared utilities

<critical_rules>
  <rule>NEVER import functions from one handler module into another. Shared logic belongs in a `utils.py` within the same package. See `apps/bot/mitup_bot/handlers/inline_query/utils.py` for an example.</rule>
</critical_rules>

## Localization

Fetch the user early in the handler — before branching on meeting existence — so `user.lang` is available in all code paths. The "never hardcode `lang=`" rule itself lives in the `user-facing-text` skill; the handler-side concern is *when* to fetch the user so the language is in scope at every `.get()` call.

## `chat_instance`

`chat_instance` is a **required** field on every `CallbackQuery`. In this project it is only **stored** on `Message` records for inline (shared) messages (see `Message.from_update` and `Message.capture_chat_instance` in `models/messages.py` — the latter fills it in on a card that was tracked at share time, when no chat was known yet). For bot-chat callback queries the value exists but is not persisted.

Access it via `update.callback_query.chat_instance`.
