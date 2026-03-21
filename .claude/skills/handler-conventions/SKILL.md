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

Every registration method requires a `handler_id` argument — a `HandlerId` enum member that uniquely identifies the handler.

## Database session

Decorate the handler with `@with_async_session` from `mitup_bot.db`. This injects a `Session` as the **first positional argument**:

```python
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry

@HandlersRegistry.register_callback_query(handler_id=MyHandlerId.SHOW)
@with_async_session
async def show(session: Session, update: Update, context: MitupContext) -> None:
    user = guards.current_user(update, session)
    ...
```

> **Type checker note:** Call sites trigger a false-positive `missing-argument` from `ty`. Suppress with `# ty: ignore[missing-argument]` and the tracking issue URL. See the `type-checking` skill.

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

Handlers accept PTB `BaseFilter` instances to narrow which updates they process. Custom filters are in `personal_filters.py` (e.g., `UserExistFilter`, `PositiveNumberFilter`).

## Handler structure

Each feature submodule typically contains:

| File | Purpose |
|------|---------|
| `enums.py` | `HandlerId` subclass with members identifying each handler in the module |
| `entry.py` | Entry-point callback (usually the conversation entry or main action) |
| Other files | Supporting handlers, views, and utilities for the feature |

## Callback data

All button interactions use `CallbackData` — a Pydantic model defined in `mitup_bot/callback_data.py`. Predefined instances for the whole bot live in `mitup_bot/utils/callbacks.py`. When adding a new handler that needs a button action, add its callback instance there.

### Formats

| Class | Format | Use when |
|-------|--------|----------|
| `CallbackData` | `{action};{entity}:{id}` | Standard action on an entity |
| `DateCallbackData` | `{action};{entity}:{id};date:{YYYY-MM-DD}` | Action involves a date (e.g., setting a meeting date) |
| `MeetingCallbackData` | `{action};{entity}:{id}:{meeting_id}` | Action targets a subject (id) within a specific meeting |

### Defining a new callback

```python
# In mitup_bot/utils/callbacks.py
from mitup_bot.callback_data import CallbackData

MY_ACTION = CallbackData(action="my_action", entity="my_entity")
```

Use `.with_id(id)` at call sites to attach a specific record ID:

```python
cb.MY_ACTION.with_id(meeting.db_id)
```

### Naming conventions

- **Destructive flows** — any action that is irreversible or results in permanent data loss (hard deletes, meeting cancellation that removes all participants, any change users cannot undo through the bot) — must follow the three-step pattern: `DELETE_<X>` (trigger) → `CONFIRM_<X>` (confirm) → `DECLINE_<X>` (decline). Non-destructive actions (e.g. editing a description that can be re-edited) do not require this pattern.
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
3. Add `@with_async_session` if database access is needed.
4. Register the handler in `tests/test_failure_modes.py` if it calls any `guards.*` function (e.g. `guards.current_user`, `guards.meeting_accessible`, `guards.valid_callback_data`, `guards.valid_meeting_callback_data`).
5. Create a dedicated test file at `tests/handlers/<package>/test_<module>.py`.
6. Import the handler module in `mitup_bot/handlers/__init__.py`.

## Removing a handler — checklist

When deleting a handler entirely:

1. Remove its `HandlerId` member from `enums.py`.
2. Delete the handler function and any callbacks/views it exclusively owns.
3. **Remove its `Context` entries from `tests/test_failure_modes.py`** — every guard call registered there becomes stale and will cause test failures if left behind.
4. Remove its import from `mitup_bot/handlers/__init__.py` if the whole module is gone.

## Shared utilities

<critical_rules>
  <rule>NEVER import functions from one handler module into another. Shared logic belongs in a `utils.py` within the same package. See `mitup_bot/handlers/inline_query/utils.py` for an example.</rule>
</critical_rules>

## Localization

Always derive the language from the user (`user.lang`) or the meeting (`meeting.lang`). Fetch the user early in the handler — before branching on meeting existence — so `user.lang` is available in all code paths.

<critical_rules>
  <rule>NEVER hard-code a language (e.g., `lang="en"`). Always derive it from `user.lang` or `meeting.lang`.</rule>
</critical_rules>

## `chat_instance`

`chat_instance` is a **required** field on every `CallbackQuery`. In this project it is only **stored** on `Message` records for inline (shared) messages (see `Message.from_update` in `models/messages.py`). For bot-chat callback queries the value exists but is not persisted.

Access it via `update.callback_query.chat_instance`.
