# Handlers

## Building a handler

A handler is an async function decorated with a `HandlersRegistry` registration method. The function receives the PTB `Update` and `MitupContext` and is invoked when a matching Telegram event arrives.

### Registration methods

| Method | Purpose |
|--------|---------|
| `register_command` | Registers a `/command` handler |
| `register_message` | Handles incoming text or media messages |
| `register_callback_query` | Handles button presses (inline keyboard callbacks) |
| `register_conversation_handler` | Multi-step conversation with states and fallbacks |
| `register_inline_handler` | Handles inline queries |

Each method accepts a `handler_id` (a `HandlerId` enum member) that uniquely identifies the handler.

### Conversation handlers

`register_conversation_handler` composes previously registered handlers into a state machine:

- `entry_points_handler_names` — handler IDs that trigger the start of the conversation.
- `states` — `dict[Enum, list[HandlerId]]` mapping state keys to handlers.
- `fallbacks` — handlers used when no state matches the incoming update.

### Filters

Handlers accept PTB `BaseFilter` instances to narrow which updates they process. Custom filters are in `personal_filters.py` (e.g., `UserExistFilter`, `PositiveNumberFilter`).

## Adding a database session

Most handlers need database access. Decorate the handler with `@with_async_session` from `mitup_bot.db`. This injects a `Session` as the **first positional argument**:

```python
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry

@HandlersRegistry.register_callback_query(handler_id=MyHandlerId.SHOW)
@with_async_session
async def show(session: Session, update: Update, context: MitupContext) -> None:
    user = guards.current_user(update, session)
    ...
```

Callers invoke the handler **without** passing `session` — the decorator provides it.

> **Type checker note:** Call sites of `@with_async_session` functions trigger a false-positive `missing-argument` from `ty` due to missing `Concatenate` support. Suppress with `# ty: ignore[missing-argument]` and a tracking issue URL. See `.agents/type-checking.md`.

## Handler structure

Handlers are organized into submodules by feature area. Each submodule typically contains:

| File | Purpose |
|------|---------|
| `enums.py` | `HandlerId` subclass with members identifying each handler in the module |
| `entry.py` | Entry-point callback (usually the conversation entry or main action) |
| Other files | Supporting handlers, views, and utilities for the feature |

The `enums.py` → `entry.py` convention is organizational only — it has no runtime effect. It makes it easy to find the starting point for any feature.

## Adding a new handler

1. Define a `HandlerId` member in the appropriate `enums.py` (or create a new submodule).
2. Write the handler function with the `@HandlersRegistry.register_*` decorator.
3. Add `@with_async_session` if database access is needed.
4. Register the handler in `tests/test_failure_modes.py` if it uses guards (`current_user`, `meeting_accessible`, `valid_callback_data`, etc.) — see `tests/AGENTS.md` for details.
5. Import the handler module in `mitup_bot/handlers/__init__.py` so the registry picks it up.
