---
name: new-migration
description: Create and validate a new Alembic database migration.
user-invocable: true
argument-hint: "[brief description of the schema change]"
allowed-tools: Read, Bash, Glob
---

Steps:
1. Inspect the model changes or task description to understand what the migration needs to do.
2. Create an empty migration scaffold: `uv run mb db migrate new "$ARGUMENTS"`
3. Open the generated migration file under `libs/data/mitup_bot/migrations/versions/`.
4. Write the `upgrade()` and `downgrade()` functions by hand. Do NOT use `--autogenerate` — migrations must be explicitly authored.
5. Review and check for:
   - Missing `server_default` for non-nullable columns on existing tables
   - PostgreSQL timestamp trigger columns (`created_time`, `updated_time`) — these are set by DB triggers, not application code
   - Nullable changes that may require data backfills
   - `downgrade()` must cleanly reverse everything `upgrade()` does
   - Backward compatibility: the migration runs before the new images roll and is not undone by an ECS rollback, so the schema must stay safe for the currently-deployed image (and for both the bot and recurrent-events services, which share one database). Keep each release additive — new columns nullable and backfilled; a `NOT NULL` needs a `server_default` or a two-step; type changes go via a new column. Split anything breaking across releases (expand → migrate → contract); never drop or rename a column in the same release that stops using it.
6. Run: `uv run mb db migrate validate` to confirm it applies cleanly.
