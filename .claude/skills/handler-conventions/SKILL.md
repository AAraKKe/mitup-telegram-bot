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

Callers invoke the handler **without** passing `session` — the decorator provides it.

> **Type checker note:** Call sites trigger a false-positive `missing-argument` from `ty`. Suppress with `# ty: ignore[missing-argument]` and the tracking issue URL. See the `type-checking` skill.

## Conversation handlers

`register_conversation_handler` composes previously registered handlers into a state machine:

- `entry_points_handler_names` — handler IDs that trigger the start of the conversation.
- `states` — `dict[Enum, list[HandlerId]]` mapping state keys to handlers.
- `fallbacks` — handlers used when no state matches the incoming update.

**Entry points must be registered before the conversation.** The registry looks up each handler ID at registration time — if a handler referenced in `entry_points_handler_names` hasn't been registered yet, `HandlerNotRegistered` is raised. When adding cross-module entry points, verify that `handlers/__init__.py` imports the entry-point module before the module that calls `register_conversation_handler`.

**Circular imports.** If module A needs an enum from module B and vice versa, extract shared enums into standalone files (e.g., `command_enums.py`, `enums.py`) or use a local import inside the function body. See `command_enums.py` (extracted `CommandsId`) and `commands.py` (local import of `ConversationMeetingState`) for examples.

**Fallbacks and exit handling:**

- **When the user can exit**: call `context.store_on_exit(ContextId.<X>, message, cancel_callback)` in the entry handler and set `fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT]`. `MESSAGE_WITHOUT_TEXT` shows an interruption view and keeps the user in state so they can cancel explicitly.
- **When the user cannot exit** (e.g., registration flows): use a dedicated fallback handler (filter `~filters.TEXT | filters.COMMAND`, registered with `bindable=False`) that informs the user and returns the same state.
- <critical_rules>
  <rule>Text handlers in conversations MUST use `filters.TEXT & ~filters.COMMAND` so commands fall through to the fallback.</rule>
</critical_rules>

## Filters

Handlers accept PTB `BaseFilter` instances to narrow which updates they process. Custom filters are in `personal_filters.py` (e.g., `UserExistFilter`, `PositiveNumberFilter`).

## Handler structure

Handlers are organized into submodules by feature area. Each submodule typically contains:

| File | Purpose |
|------|---------|
| `enums.py` | `HandlerId` subclass with members identifying each handler in the module |
| `entry.py` | Entry-point callback (usually the conversation entry or main action) |
| Other files | Supporting handlers, views, and utilities for the feature |

## Adding a new handler — checklist

1. Define a `HandlerId` member in the appropriate `enums.py` (or create a new submodule).
2. Write the handler function with the `@HandlersRegistry.register_*` decorator.
3. Add `@with_async_session` if database access is needed.
4. Register the handler in `tests/test_failure_modes.py` if it uses guards.
5. Create a dedicated test file at `tests/handlers/<package>/test_<module>.py`.
6. Import the handler module in `mitup_bot/handlers/__init__.py`.

## Shared utilities

If multiple handlers in the same package need shared logic, extract it into a `utils.py` within that package. See `mitup_bot/handlers/inline_query/utils.py` for an example.

<critical_rules>
  <rule>NEVER import functions from one handler module into another. Shared logic belongs in a `utils.py` within the same package.</rule>
</critical_rules>

## Localization

Always derive the language from the user (`user.lang`) or the meeting (`meeting.lang`). Fetch the user early in the handler — before branching on meeting existence — so `user.lang` is available in all code paths.

<critical_rules>
  <rule>NEVER hard-code a language (e.g., `lang="en"`). Always derive it from `user.lang` or `meeting.lang`.</rule>
</critical_rules>

## `chat_instance`

`chat_instance` is a **required** field on every `CallbackQuery`. In this project it is only **stored** on `Message` records for inline (shared) messages (see `Message.from_update` in `models/messages.py`). For bot-chat callback queries the value exists but is not persisted.

Access it via `update.callback_query.chat_instance`.
