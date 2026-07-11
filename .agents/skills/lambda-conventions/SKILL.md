---
name: lambda-conventions
description: AWS Lambda function conventions for mitup_bot. Auto-load when writing, editing, or reviewing Lambda apps under apps/lambda-*/.
user-invocable: false
---

# Lambda Conventions

Each AWS Lambda is its own workspace **application** under `apps/lambda-*/`, shipping a single handler module into the shared `mitup_bot.lambdas` namespace (a PEP 420 namespace package — no `__init__.py`). This keeps the frozen handler dotted paths (e.g. `mitup_bot.lambdas.migrations.run_migrations`, referenced by the external infra repo) valid while each app declares only the dependencies its handler needs. Lambdas run outside the PTB application lifecycle and have different constraints than bot handlers.

## Inventorying what exists

Rather than maintaining a list here (which goes stale), inspect the `apps/lambda-*/` members directly — each ships one handler module under `mitup_bot/lambdas/`. At the time of writing: `apps/lambda-migrations` ships `mitup_bot/lambdas/migrations.py` (runs Alembic upgrade/downgrade; deliberately PTB-free) and `apps/lambda-alarm` ships `mitup_bot/lambdas/alarm_action.py` (forwards CloudWatch alarms to GitLab). Each app's `mb ci check-import-isolation` entry proves its dependency closure; both lambda apps additionally assert PTB is absent (`forbidden_imports=("telegram",)`).

## Constraints

- **No `MitupRuntime`** — lambdas cannot use the full bot bootstrap. They set up DB, config, and API independently.
- **`BotAdapter` instead of `MitupContext`** — there is no PTB `Application`, so Telegram API access goes through `BotAdapter`. See the `api-wrapper` skill for how to construct it and supply (or null) a `MetricsClient`.
- **Cold starts** — lambdas may be invoked infrequently. Keep initialization lightweight and avoid global state that assumes warm execution.
- **Execution time limits** — AWS Lambda has a configurable timeout. Long-running tasks should be broken into smaller units or use ECS instead.

## Adding a new lambda

1. Create a new `apps/lambda-<name>/` member: a `pyproject.toml` (static version, `packages = ["mitup_bot"]`, only the deps the handler needs) and `apps/lambda-<name>/mitup_bot/lambdas/<name>.py` — no `mitup_bot/__init__.py`, no `mitup_bot/lambdas/__init__.py`. Register the member in the root `[tool.uv.workspace]`, `[tool.uv.sources]`, the `workspace` dependency-group, the `ty` include list, the CI stub flow (`Dockerfile.ci`, `.ci-docker-files`), and `import_isolation.MEMBERS`.
2. Define a handler function following the AWS Lambda signature: `def handler(event: dict[str, Any], context: Any) -> ...`
3. Use Pydantic models to validate the incoming `event` (see the existing lambda files for the pattern).
4. For database access, call `configure_db()` from `mitup_bot.db` during initialization and decorate with the async `@with_session` — see the `database` skill for the full convention.
5. For Telegram API access, wrap the `ExtBot` with `BotAdapter` / `build_api()` — see the `api-wrapper` skill.
6. Update the AWS infrastructure configuration in the separate infrastructure repository to deploy the new lambda.
