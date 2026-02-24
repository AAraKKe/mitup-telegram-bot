---
name: new-migration
description: Create and validate a new Alembic database migration.
user-invocable: true
argument-hint: "[brief description of the schema change]"
allowed-tools: Read, Bash, Glob
---

Steps:
1. Inspect recent model changes to understand what tables/columns changed.
2. Run: `alembic revision --autogenerate -m "$ARGUMENTS"`
3. Open the generated migration file under `mitup_bot/migrations/versions/`.
4. Review and check for:
   - Missing `server_default` for non-nullable columns
   - PostgreSQL timestamp trigger columns (don't add those manually)
   - Nullable changes that may require data backfills
5. Run: `hatch run dev:validate-migrations` to confirm it applies cleanly.
