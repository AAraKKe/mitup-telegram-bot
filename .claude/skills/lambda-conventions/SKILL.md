---
name: lambda-conventions
description: AWS Lambda function conventions for mitup_bot. Auto-load when writing, editing, or reviewing Lambda functions in mitup_bot/lambdas/.
user-invocable: false
---

# Lambda Conventions

AWS Lambda functions live in `mitup_bot/lambdas/`. They run outside the PTB application lifecycle and have different constraints than bot handlers.

## Inventorying what exists

Rather than maintaining a list here (which goes stale as lambdas are added or removed), inspect `mitup_bot/lambdas/` directly — each `.py` file there is a lambda handler. Read the module docstring or the `handler()` signature for what it does. At the time of writing the directory contains `migrations.py` (runs Alembic upgrade/downgrade from a Lambda event); anything else you see there is newer.

## Constraints

- **No `MitupRuntime`** — lambdas cannot use the full bot bootstrap. They set up DB, config, and API independently.
- **`BotAdapter` instead of `MitupContext`** — there is no PTB `Application`, so Telegram API access goes through `BotAdapter`. See the `api-wrapper` skill for how to construct it and supply (or null) a `MetricsClient`.
- **Cold starts** — lambdas may be invoked infrequently. Keep initialization lightweight and avoid global state that assumes warm execution.
- **Execution time limits** — AWS Lambda has a configurable timeout. Long-running tasks should be broken into smaller units or use ECS instead.

## Adding a new lambda

1. Create a new file in `mitup_bot/lambdas/`.
2. Define a handler function following the AWS Lambda signature: `def handler(event: dict[str, Any], context: Any) -> ...`
3. Use Pydantic models to validate the incoming `event` (see the existing lambda files for the pattern).
4. For database access, call `configure_db()` from `mitup_bot.db` during initialization and decorate with `@with_session` / `@with_async_session` — see the `database` skill for the full convention.
5. For Telegram API access, wrap the `ExtBot` with `BotAdapter` / `build_api()` — see the `api-wrapper` skill.
6. Update the AWS infrastructure configuration in the separate infrastructure repository to deploy the new lambda.
