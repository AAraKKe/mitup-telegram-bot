# DB Integration Tests

## Overview

DB integration tests run against a real Postgres container via `testcontainers`. They live in `tests/db/` and require Docker to be running.

## Running

```bash
# Run all DB tests
hatch run dev:test-db

# Run a single test
hatch run dev:test-db -- -k "test_name" -v
```

DB tests are skipped during normal `hatch run dev:test` runs via the `--db-tests` flag logic in `conftest.py`.

## Fixtures

### `db_session` (session-scoped)

Yields a live `Session` via `db.begin()`. Use this **instead of** `mock_session`. All queries hit the real database.

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

## Raw SQL rules

- Use `session.exec(text(...))` — never `session.execute()` (triggers SQLModel deprecation warnings).
- Bind parameters with `.bindparams()`:

```python
from sqlalchemy import text

result = session.exec(
    text("SELECT * FROM users WHERE tg_user_id = :uid").bindparams(uid=999_001)
)
```

## Migrations

Do not hardcode migration revision constants. The `test_no_pending_migrations` test reads the expected head dynamically from Alembic's `ScriptDirectory` and asserts the `alembic_version` table matches.
