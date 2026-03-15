---
name: coding-standards
description: Python coding standards for mitup_bot. Auto-load when writing or reviewing any Python code in this project.
user-invocable: false
---

# Coding Standards

These standards are derived from the actual patterns used in this codebase. Deviations are bugs — not style preferences.

## Hard antipatterns

These are never acceptable. Check these first before writing or reviewing any code.

<critical_rules>
  <rule>No methods longer than ~30 lines (handlers) or ~20 lines (helpers). Split by responsibility.</rule>
  <rule>No nesting deeper than 2 levels — use early return or extract a helper.</rule>
  <rule>No leading underscore on module-level functions. A module-level function is either public (no underscore) or a class member (underscore allowed). Never use `_` on a function defined at module level.</rule>
  <rule>No single-letter or abbreviated variable names outside of loop indices and explicitly mathematical contexts — name variables after what they represent.</rule>
  <rule>`# ---` banners only in large files where sections are clearly distinct and hard to navigate without them (e.g. long test modules or large utility files).</rule>
  <rule>No module docstrings on handler, model, or view files.</rule>
  <rule>No `Optional[X]`, `List[X]`, `Dict[K, V]` — use modern union syntax and built-in generics. See [Type hints](#type-hints).</rule>
  <rule>No `pass` — use `...` in empty bodies.</rule>
  <rule>No `print()` — use `logging`. See [Logging](#logging).</rule>
  <rule>No hardcoded language strings — always derive from `user.lang` or `meeting.lang`.</rule>
  <rule>No hardcoded user-facing text — always use `Messages` classes from `messages.py`.</rule>
  <rule>No star imports (`from module import *`). Import only what is used.</rule>
  <rule>`else/elif` chains with more than 2 branches must use `match` instead.</rule>
</critical_rules>

---

## Single responsibility

<critical_rules>
  <rule>If a method needs a multi-line comment to explain what the next block does, extract that block into a function with a descriptive name.</rule>
  <rule>Shared logic between two functions in the same package goes in a `utils.py` within that package. NEVER cross-import handler modules into each other.</rule>
</critical_rules>

Use early return to avoid nesting:

```python
if meeting is None:
    return

await context.api.edit_message(update=update, view=meeting.main_view)
```

## Naming conventions

| Symbol | Convention | Example |
|--------|-----------|---------|
| Callback query handlers | `callback_query_<action>` | `callback_query_show_meeting` |
| Message handlers | `<action>_message_handler` | `edit_title_meeting_message_handler` |
| Command handlers | `<action>_command` | `start_command` |
| Module-level helpers | `snake_case` (no leading underscore) | `make_message_update` |
| Truly private class internals | `_snake_case` | `_freeze`, `_build_key` |
| Private inner dataclasses | `_PascalCase` | `_MarkerSpan` |
| Type aliases | `PascalCase` | `Keyboard`, `TMitupContext` |
| Section separators | `# ---` with a label | `# --- UTF-16 helper ---` |

Variable names must describe what the variable holds, not its type or position:

```python
# Bad
f = ValidTitleFilter()
e1 = DateTimeMessageEntity(offset=0, length=8, unix_time=unix_ts)
e2 = DateTimeMessageEntity(offset=9, length=5, unix_time=unix_ts)

# Good
title_filter = ValidTitleFilter()
first_date_entity = DateTimeMessageEntity(offset=0, length=8, unix_time=unix_ts)
second_date_entity = DateTimeMessageEntity(offset=9, length=5, unix_time=unix_ts)
```

## Docstrings

Write a docstring only when the function name and signature do not convey the full intent.

| Situation | Write a docstring? |
|-----------|-------------------|
| Trivial accessor or one-liner | No |
| Side-effects that aren't obvious from the name | Yes |
| Non-trivial algorithm or constraint | Yes |
| Module with complex design decisions | Short paragraph at the top after imports|
| Handler entry point | No — the decorator and name are sufficient |

<critical_rules>
  <rule>Docstrings must be short (1–3 sentences max). No `Args:` / `Returns:` sections unless a parameter has a non-obvious contract.</rule>
  <rule>Explain *why* a non-obvious decision was made — never narrate *what* the code does.</rule>
</critical_rules>

**Good:**

```python
def utf16_len(s: str) -> int:
    """Return the length of *s* measured in UTF-16 code units.

    Telegram entity offsets are expressed in UTF-16 code units, not Unicode
    code points. Characters outside the BMP (e.g. emoji) occupy two units.
    """
    return len(s.encode("utf-16-le")) // 2
```

**Bad:**

```python
def utf16_len(s: str) -> int:
    """
    Calculate the UTF-16 length of the given string.

    Args:
        s: The string to calculate the length of.

    Returns:
        The length of the string in UTF-16 code units.
    """
    return len(s.encode("utf-16-le")) // 2
```

## Type hints

Always required. Use modern union syntax and built-in generics (see [Hard antipatterns](#hard-antipatterns)). Always declare an explicit return type when the function returns a value (`-> T`). `-> None` is implicit and **must not** be written — omitting it is the correct style.

**Conversation handler return types.** `ConversationMeetingState` is a plain `Enum`, not `IntEnum` — its members are **not** `int`. Handlers that return both a state and `ConversationHandler.END` (which is `int`) must be typed as `-> ConversationMeetingState | int`, not `-> int`:

```python
async def callback_query_set_meeting_time(...) -> ConversationMeetingState | int:
    ...
    return ConversationMeetingState.EDIT_TIME   # Enum, not int
    ...
    return ConversationHandler.END              # int (-1)
```

Handlers that only ever return a state (fallback handlers) should be typed `-> ConversationMeetingState`.

Use `TYPE_CHECKING` for forward references that would cause circular imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .users import User
```

Type aliases belong at module level and use `PascalCase`:

```python
ButtonRow = list[ButtonConfig]
Keyboard = list[ButtonRow]
```

## Python idioms

**Walrus operator** — for optional lookups that gate further work:

```python
if user := User.by_tg_user_id(session, tg_id):
    return user
raise UserNotFound(tg_id)
```

**`match` statements** — for exhaustive case analysis. Always include an `assert_never` arm on unreachable branches:

```python
match self.position:
    case PaginatedViewPosition.FIRST:
        return [go_forward]
    case PaginatedViewPosition.LAST:
        return [go_back]
    case _ as unreachable:
        assert_never(unreachable)
```

**`assert` for invariants** — for conditions that *must* hold at that point due to framework guarantees, not user input:

```python
assert update.effective_message is not None
assert title is not None, "TEXT filter ensures this is set"
```

Do NOT use `assert` for validation that can fail on valid user input. Use guards instead.

**Properties for computed attributes** — derived values belong on the model as `@property`:

```python
@property
def n_participants(self) -> int:
    """Number of participants. Does not count the waiting list."""
    return sum(not link.is_waiting_list for link in self.joined_links)
```

**`...` not `pass`** — for empty class/function bodies:

```python
class InvalidUserData(RuntimeError): ...
```

**`extend` instead of multiple `append` calls**

```python
entities.extend(
    (
        MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length),
        MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length),
    )
)
```

**Comprehensions instead of `for` loops** — always prefer comprehensions when possible. Only use `for` loops when there is logic in each iteration or the comprehension would require more than 2 nested loops.

**Enum over string literals** — always prefer enums over bare strings for anything that represents a fixed set of values.

## Imports

Follow standard grouping: stdlib → third-party → first-party. Relative imports for same-package files.

Alias frequently used modules at the import line:

```python
from mitup_bot.utils import callbacks as cb
```

## Comments

Use comments to explain *why*, not *what*. The code already shows what is happening.

**Good:**

```python
# We bypass the freeze guard — MessageEntity calls _freeze() in __init__
object.__setattr__(self, "unix_time", unix_time)
```

**Bad:**

```python
# Set the unix_time attribute
object.__setattr__(self, "unix_time", unix_time)
```

## Data structure choices

| Use | When |
|-----|------|
| `@dataclass` | Simple value objects with no validation or serialization needs (e.g. `MitupView`, `Bold`, `Link`) |
| `BaseModel` (Pydantic) | Objects needing validation, coercion, or serialization (e.g. `ButtonConfig`, `CallbackData`) |
| `SQLModel` | Database-mapped tables |
| `StrEnum` | Metric keys, feature flags, string-valued enumerations |

## Exception definitions

<critical_rules>
  <rule>Inherit from the semantically correct base: `RuntimeError` for domain errors, `ValueError` for bad input, `AttributeError` for wrong state access.</rule>
  <rule>Include enough context in the message to diagnose the problem without a debugger: IDs, repr values, and the action being attempted.</rule>
</critical_rules>

```python
class MalformedCallbackData(RuntimeError):
    def __init__(self, handler: HandlerId, callback_data: CallbackData) -> None:
        super().__init__(
            f"Callback data {callback_data!r} received in handler {handler!r} is malformed."
        )
```

## Logging

```python
logging.debug("Enter into <handler_name>")   # at handler entry points only
logging.warning(message)                      # unexpected-but-recoverable situations
```
