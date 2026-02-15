# Lambdas

AWS Lambda functions live in `mitup_bot/lambdas/`. They run outside the PTB application lifecycle and have different constraints than bot handlers.

## Current functions

| File | Purpose |
|------|---------|
| `migrations.py` | Runs Alembic database migrations (upgrade/downgrade) triggered by a Lambda event |

## Constraints

- **No `MitupRuntime`** — lambdas cannot use the full bot bootstrap. They set up DB, config, and API independently.
- **`BotAdapter` instead of `MitupContext`** — since there is no PTB `Application`, use `BotAdapter` for Telegram API access. Note that `BotAdapter` metrics methods are no-ops; if metrics are needed, use `MitupMetricsEngine` directly (see `.agents/monitoring.md`).
- **Cold starts** — lambdas may be invoked infrequently. Keep initialization lightweight and avoid global state that assumes warm execution.
- **Execution time limits** — AWS Lambda has a configurable timeout. Long-running tasks should be broken into smaller units or use ECS instead.

## Adding a new lambda

1. Create a new file in `mitup_bot/lambdas/`.
2. Define a handler function following the AWS Lambda signature: `def handler(event: dict[str, Any], context: Any) -> ...`
3. Use Pydantic models to validate the incoming `event` (see `MigrationEvent` in `migrations.py` for the pattern).
4. For database access, use `configure_db()` from `mitup_bot.db` and `@with_session` / `@with_async_session`.
5. For Telegram API access, construct an `ExtBot` and wrap it with `BotAdapter` / `build_api()`.
6. Update the AWS infrastructure configuration in the separate infrastructure repository to deploy the new lambda.
