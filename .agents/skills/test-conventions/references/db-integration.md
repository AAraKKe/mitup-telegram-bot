# DB Integration Tests

## Overview

DB integration tests run against a real Postgres container via `testcontainers`. They live in `tests/data/db_behavior/` and require Docker to be running.

## Running

```bash
# Run all DB tests
uv run mb test --db

# Run a single test
uv run mb test --db -k "test_name" -v
```

DB tests are skipped during normal `uv run mb test` runs via the `--db-tests` flag logic in `conftest.py`.

## Fixtures

### `db_session` (session-scoped)

Yields a live `AsyncSession` via `async with db.begin()`, on the session-scoped event loop. Use this **instead of** `mock_session`. All queries hit the real database and every session I/O call is awaited.

### Seed data (session-scoped, auto-flushed)

| Fixture | Details |
|---|---|
| `seed_user` | `tg_user_id=999_001` + Settings |
| `seed_second_user` | `tg_user_id=999_002` + Settings |
| `seed_meetup` | Owned by `seed_user` |
| `seed_joined_link` | Links `seed_second_user` to `seed_meetup` |

### Data collision rule

When creating throwaway objects (e.g., testing cascade deletes), use `tg_user_id=998_00x` to avoid colliding with the `999_00x` seed data.

```python
# Good — uses 998 range for throwaway data
throwaway_user = User(tg_user_id=998_001, ...)

# Bad — collides with seed_user
throwaway_user = User(tg_user_id=999_001, ...)
```

Multi-session concurrency tests use the `997_0xx` range for **committed** per-test data: the `999_00x` seeds live in the session fixture's open transaction (invisible to other sessions) and `998_xxx` throwaways stay inside a single rolled-back session, so races across concurrent `db.begin()` transactions need data that is actually committed. Because that data outlives its transaction, each test must claim its own sub-range and delete everything it committed in a `finally`-guarded committed transaction (see `tests/data/db_behavior/test_meeting_row_locks.py`).

## Raw SQL rules

- Use `await session.exec(text(...))` — never `session.execute()` (triggers SQLModel deprecation warnings).
- Bind parameters with `.bindparams()`:

```python
from sqlalchemy import text

result = await session.exec(
    text("SELECT * FROM users WHERE tg_user_id = :uid").bindparams(uid=999_001)
)
```

## Migrations

Do not hardcode migration revision constants. The `test_no_pending_migrations` test reads the expected head dynamically from Alembic's `ScriptDirectory` and asserts the `alembic_version` table matches.
