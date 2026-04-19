---
name: database
description: Database layer conventions. Auto-load when writing or modifying models, sessions, migrations, or @with_session/@with_async_session decorators.
user-invocable: false
---

# Database

## Engine and sessions

The database layer lives in `mitup_bot/db.py`. It uses SQLAlchemy with SQLModel and manages sessions through a `sessionmaker` configured at startup via `configure_db()`.

**Never create sessions manually.** Use the session-injecting decorators instead.

## Session decorators

Two decorators inject a `Session` as the first positional argument and wrap the call in a transaction (commit on success, rollback on exception):

| Decorator | Use case |
|-----------|----------|
| `with_session` | Synchronous functions |
| `with_async_session` | Async functions (handlers, CLI commands) |

```python
from mitup_bot.db import with_async_session
from sqlmodel import Session

@with_async_session
async def my_handler(session: Session, update: Update, context: MitupContext) -> int:
    user = session.get(User, user_id)
    ...

# Call without the session argument — the decorator supplies it:
await my_handler(update, context)
```

<note>
`ty` does not yet support `Concatenate` / `ParamSpec`, so call sites produce a false-positive `missing-argument` error. Suppress with `# ty: ignore[missing-argument]` and include the tracking issue URL. See the `type-checking` skill for the full convention.
</note>

## Models

All SQLModel table models live in `mitup_bot/models/` and use SQLModel. Inspect `mitup_bot/models/__init__.py` for the current list of exported models.

Key patterns:

- All models inherit from `BaseModel` (in `base_model.py`) which provides the `db_id` property.
- Models with JSON columns that need mutation tracking extend `MutableModel` (in `mutable_model.py`).
- Pydantic models (e.g., `MeetupLocation`, `MessageButtons`) are used for structured JSON fields and are not SQLModel tables.

When adding a new model:

1. Create the model class in the appropriate file under `mitup_bot/models/`.
2. Export it from `mitup_bot/models/__init__.py`.
3. Generate an Alembic migration (see [Migrations](#migrations) below).
4. Add test helpers in `tests/helpers/` if the model is referenced in 2 or more test modules or requires more than 2 constructor arguments.

## Migrations

Database migrations use [Alembic](https://alembic.sqlalchemy.org/). Migration scripts live in `mitup_bot/migrations/versions/`.

Day-to-day commands when running the bot locally:

```bash
hatch run dev:migrations-upgrade    # Apply pending migrations
hatch run dev:migrations-downgrade  # Roll back one migration
hatch run dev:validate-migrations   # Validate migration graph integrity
```

When a model change needs a new migration, invoke the `new-migration` skill — it walks through scaffolding the revision file, writing `upgrade()` / `downgrade()` by hand (autogenerate is disallowed), and validating that both paths apply cleanly. Don't repeat the walkthrough here; that skill owns it.

## Automatic timestamps

PostgreSQL triggers (`set_created_time()`, `set_updated_time()`) defined in migration `65b4c46d9141` set `CURRENT_TIMESTAMP` on insert and update respectively. Application code never assigns these fields.

Model classes declare them as `dt.datetime | None = None` because Python cannot know about DB-level triggers at import time. In production every persisted row has non-`None` values. Test fixtures (e.g., `create_meetup`) supply a default `created_time` to mirror this invariant.
