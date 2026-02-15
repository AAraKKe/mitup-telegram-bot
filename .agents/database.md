# Database

## Engine and sessions

The database layer lives in `mitup_bot/db.py`. It uses SQLAlchemy with SQLModel and manages sessions through a `sessionmaker` configured at startup via `configure_db()`.

Sessions are **never created manually** inside handlers or business logic. Instead, use the session-injecting decorators described below.

## Session decorators

Two decorators inject a `Session` as the first positional argument of the wrapped function:

| Decorator | Use case |
|-----------|----------|
| `with_session` | Synchronous functions |
| `with_async_session` | Async functions (handlers, CLI commands) |

Both open a transaction that is committed on success and rolled back on exception.

### Usage

```python
from mitup_bot.db import with_async_session
from sqlmodel import Session

@with_async_session
async def my_handler(session: Session, update: Update, context: MitupContext) -> int:
    user = session.get(User, user_id)
    ...
```

Callers invoke the function **without** the `session` argument — the decorator supplies it:

```python
await my_handler(update, context)
```

> **Type checker note:** `ty` does not yet support `Concatenate` / `ParamSpec`, so call sites will produce a false-positive `missing-argument` error. Suppress with `# ty: ignore[missing-argument]` and include the tracking issue URL. See `.agents/type-checking.md` for the full convention.

## Models

All database models live in `mitup_bot/models/` and use SQLModel. Inspect `mitup_bot/models/__init__.py` for the current list of exported models — the list below may be outdated.

Key patterns:

- All models inherit from `BaseModel` (in `base_model.py`) which provides the `db_id` property.
- Models with JSON columns that need mutation tracking extend `MutableModel` (in `mutable_model.py`).
- Pydantic models (e.g., `MeetupLocation`, `MessageButtons`) are used for structured JSON fields and are not SQLModel tables.

When adding a new model:

1. Create the model class in the appropriate file under `mitup_bot/models/`.
2. Export it from `mitup_bot/models/__init__.py`.
3. Generate an Alembic migration (see Migrations below).
4. Add test helpers in `tests/helpers/` if the model needs factory functions for tests.

## Migrations

Database migrations use [Alembic](https://alembic.sqlalchemy.org/). Migration scripts live in `mitup_bot/migrations/versions/`.

Commands:

```bash
hatch run dev:migrations-upgrade    # Apply pending migrations
hatch run dev:migrations-downgrade  # Roll back one migration
hatch run dev:validate-migrations   # Validate migration graph integrity
```

When adding or modifying a model, generate a new migration and verify the upgrade/downgrade paths work correctly.
