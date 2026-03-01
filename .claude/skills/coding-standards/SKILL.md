---
name: coding-standards
description: Python coding standards for mitup_bot. Auto-load when writing or reviewing any Python code in this project.
user-invocable: false
---

# Coding Standards

These standards are derived from the actual patterns used in this codebase. Deviations are bugs — not style preferences.

## Single responsibility

<critical_rules>
  <rule>A function or method that does more than one thing must be split. The threshold is ~30 lines for handlers, ~20 for helpers. If you need to scroll to read it, it is too long.</rule>
  <rule>If a method needs a multi-line comment to explain what the next block does, that block belongs in a separate function with a descriptive name.</rule>
  <rule>Shared logic between two functions in the same package goes in a `utils.py` within that package. NEVER cross-import handler modules into each other.</rule>
</critical_rules>

Use early return to avoid nesting. Prefer:

```python
if meeting is None:
    return

await context.api.edit_message(update=update, view=meeting.main_view)
```

Over nested `if meeting is not None: ...` blocks.

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
  <rule>NEVER write a module docstring for handler files, model files, or view files. The code speaks for itself.</rule>
  <rule>Docstrings must be short (1–3 sentences max). No `Args:` / `Returns:` sections unless a parameter has a non-obvious contract.</rule>
  <rule>Do NOT narrate what the code does in comments or docstrings. Explain *why* a non-obvious decision was made, not *what* the line does.</rule>
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

Always required. Use modern Python syntax — no legacy forms.

| Wrong | Correct |
|-------|---------|
| `Optional[X]` | `X \| None` |
| `Union[X, Y]` | `X \| Y` |
| `List[X]`, `Dict[K, V]`, `Tuple[X, Y]` | `list[X]`, `dict[K, V]`, `tuple[X, Y]` |
| No return type | Explicit `-> T` or `-> None` |

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

<critical_rules>
  <rule>Avoid `else/elif` statements when more than 2 are used. Always prefer `match` statements instead.</rule>
</critical_rules>

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

Over:

```python
entities.append(MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length))
entities.append(MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length))
```

**Comprehensions instead of `for` loops** - Always prefer comprehensions over `for` loops when possible.

Only use `for` loops when there is logic in each iteration or the comprehensions become complicated (more than 2 nested loops).

```python
entities = [
    MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length)
    for offset, length in offsets_and_lengths
]
```

Over:

```python
entities = []
for offset, length in offsets_and_lengths:
    entities.append(MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length))
```

**Enum over string literals** - Always prefer enums over string literals when possible.

```python
@dataclass
class _MarkerSpan:
    outer_start: int
    outer_end: int
    inner_start: int
    inner_end: int
    types: list[EntityType]
```

Over:

```python
class _MarkerSpan:
    outer_start: int
    outer_end: int
    inner_start: int
    inner_end: int
    types: list[str]
```

## Naming conventions

| Symbol | Convention | Example |
|--------|-----------|---------|
| Callback query handlers | `callback_query_<action>` | `callback_query_show_meeting` |
| Message handlers | `<action>_message_handler` | `edit_title_meeting_message_handler` |
| Command handlers | `<action>_command` | `start_command` |
| Private helpers | `_snake_case` | `_nearest_utf16` |
| Private inner dataclasses | `_PascalCase` | `_MarkerSpan` |
| Type aliases | `PascalCase` | `Keyboard`, `TMitupContext` |
| Section separators | `# ---` with a label | `# --- UTF-16 helper ---` |

## Imports

Follow standard grouping: stdlib → third-party → first-party. Relative imports for same-package files.

Alias frequently used modules at the import line:

```python
from mitup_bot.utils import callbacks as cb
```

<critical_rules>
  <rule>No star imports (`from module import *`).</rule>
  <rule>Import only what is used. Remove unused imports.</rule>
</critical_rules>

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

Use section separators (`# -----`) only in large utility/infrastructure modules with clearly distinct sections. Handler and model files do not need them.

## Logging

```python
logging.debug("Enter into <handler_name>")   # at handler entry points only
logging.warning(message)                      # unexpected-but-recoverable situations
```

Never use `print()` in production code.

## Hard antipatterns

<critical_rules>
  <rule>No methods longer than ~30 lines. Split by responsibility.</rule>
  <rule>No nesting deeper than 2 levels — use early return or extract a helper.</rule>
  <rule>No long module docstrings on handler, model, or view files.</rule>
  <rule>No `Optional[X]`, `List[X]`, `Dict[K, V]` — use modern union syntax and built-in generics.</rule>
  <rule>No `pass` — use `...` in empty bodies.</rule>
  <rule>No `print()` — use `logging`.</rule>
  <rule>No hardcoded language strings — always derive from `user.lang` or `meeting.lang`.</rule>
  <rule>No hardcoded user-facing text — always use `Messages` classes from `messages.py`.</rule>
</critical_rules>
