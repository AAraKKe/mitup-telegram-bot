# DB Integration Tests

These are integration tests that run against a real Postgres container spun up by
[testcontainers](https://testcontainers-python.readthedocs.io/). They validate the DB schema,
ORM relationships, triggers, and FK constraints as they actually exist in production.

## How to run

```bash
# Run DB tests (Docker must be running):
hatch run dev:test-db

# Run a single test:
hatch run dev:test-db -- -k "test_no_pending_migrations" -v
```

## Why separate from the normal test suite

- **Docker dependency** — DB tests need a running Docker daemon. Most unit/handler tests don't.
- **No xdist** — Session-scoped fixtures can't be shared across xdist workers. The `test-db` script
  uses `--dist no` to run single-process.
- **Slow start-up** — Container pull + Alembic upgrade takes a few seconds; not worth running on
  every `dev:test` invocation.

## `--db-tests` flag

The flag is registered in this `conftest.py` and controls whether DB tests run:

| Command | Behaviour |
|---------|-----------|
| `hatch run dev:test` | All `@pytest.mark.db_test` tests are **SKIPPED** |
| `hatch run dev:test-db` | DB tests run; Docker unavailable → immediate session failure |

## Fixture chain

```
pytest_collection_modifyitems  → skip all db_test items when --db-tests absent;
                                  fail immediately if Docker unavailable when present

pg_container      (session)    → starts PostgresContainer using the image from docker-compose.yaml
live_db_config    (session)    → builds DbConfig from container coordinates
migrated_db       (session)    → sets MITUPBOT__DB__* env vars, runs alembic upgrade head
db_session        (session)    → calls db.configure_db(), yields a single Session via db.begin()

seed_user         (session)    → User tg_user_id=999_001 + Settings, flushed
seed_second_user  (session)    → User tg_user_id=999_002 + Settings, flushed
seed_meetup       (session)    → Meetup owned by seed_user, flushed
seed_joined_link  (session)    → JoinedUsers linking seed_second_user → seed_meetup, flushed
```

Cascade-delete throwaway objects use `tg_user_id=998_00x` to avoid collisions with seed data.

## Test modules

| File | What it covers |
|------|----------------|
| `test_tables.py` | Table existence (parametrized) |
| `test_columns.py` | Column existence and data types |
| `test_seed_data.py` | ORM round-trips on seeded rows |
| `test_relationships.py` | SQLModel relationship accessors |
| `test_timestamps.py` | DB trigger-set `created_time` / `updated_time` |
| `test_constraints.py` | NOT NULL constraints (schema introspection) |
| `test_cascades.py` | ON DELETE CASCADE behaviour |
| `test_fk.py` | Nullable FK and FK violation enforcement |
| `test_migrations.py` | Alembic head revision assertion |

All raw-SQL tests use `session.exec(text(...))` (not `session.execute()`) to avoid
SQLModel's deprecation warning. `text()` parameters are bound with `.bindparams()`.

## Migration head check

`test_no_pending_migrations` reads the expected head revision dynamically from Alembic's
`ScriptDirectory` (via `alembic.ini`) and asserts that `alembic_version` contains exactly that
revision. No constant needs to be maintained — adding a new migration automatically updates what
the test considers the expected state.
