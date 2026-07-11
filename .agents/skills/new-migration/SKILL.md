---
name: new-migration
description: Create and validate a new Alembic database migration.
user-invocable: true
argument-hint: "[brief description of the schema change]"
allowed-tools: Read, Bash, Glob
---

Steps:
1. Inspect the model changes or task description to understand what the migration needs to do.
2. Create an empty migration scaffold: `alembic revision -m "$ARGUMENTS"`
3. Open the generated migration file under `mitup_bot/migrations/versions/`.
4. Write the `upgrade()` and `downgrade()` functions by hand. Do NOT use `--autogenerate` — migrations must be explicitly authored.
5. Review and check for:
   - Missing `server_default` for non-nullable columns on existing tables
   - PostgreSQL timestamp trigger columns (`created_time`, `updated_time`) — these are set by DB triggers, not application code
   - Nullable changes that may require data backfills
   - `downgrade()` must cleanly reverse everything `upgrade()` does
6. Run: `uv run mb db migrate validate` to confirm it applies cleanly.
