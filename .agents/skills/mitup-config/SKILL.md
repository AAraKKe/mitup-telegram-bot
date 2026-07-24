---
name: config
description: Configuration provider system. Auto-load when dealing with config fields, new environments, or SecretStr values.
user-invocable: false
---

# Configuration

The configuration system lives in `libs/core/mitup_bot/config.py`. It uses a multi-provider merge strategy with Pydantic validation.

## How it works

`Config.from_providers()` accepts multiple `ConfigProvider` instances and merges their output. Providers are processed in **reverse order** — the **first** provider in the argument list has the highest priority.

The standard bootstrap in `MitupRuntime` uses:

```python
Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(env))
```

This means **environment variables override TOML file values**.

## Providers

### `TomlConfigProvider`

Reads `libs/core/mitup_bot/environments/{env}.toml` where `env` is a member of the `Env` enum (`DEV`, `PROD`). TOML sections map to config groups.

### `EnvVariablesConfigProvider`

Reads environment variables matching the naming convention:

```
MITUPBOT__<GROUP>__<KEY>=<VALUE>
```

- Prefix: `MITUPBOT__` (double underscore separators)
- `<GROUP>` maps to a config section (case-insensitive, converted to lowercase)
- `<KEY>` maps to a field within that section

Examples:

```bash
MITUPBOT__DB__USERNAME=postgres          # → config.db.username
MITUPBOT__BOT__TOKEN=abc123              # → config.bot.token
MITUPBOT__METRICS__NAMESPACE=MitupBot    # → config.metrics.namespace
MITUPBOT__APP__RUN_MODE=polling          # → config.app.run_mode
```

Values are auto-converted: `"true"`/`"false"` → `bool`, numeric strings → `int`/`float`, everything else stays `str`.

## Config sections

| Section | Class | Purpose |
|---------|-------|---------|
| `db` | `DbConfig` | Database connection (username, password, url, port, database) |
| `bot` | `BotConfig` | Telegram bot token, webhook domain/port/secret, rate limits, update-concurrency cap |
| `google_api` | `GoogleApiConfig` | Google Maps geocoding and timezone API keys |
| `app` | `AppConfig` | Run mode (`POLLING` or `WEBHOOK`) |
| `metrics` | `MetricsConfig` | CloudWatch namespace, metrics environment, flush behavior |

## Cross-section invariants

Invariants spanning multiple sections live as `model_validator`s on `Config` itself (per-field rules stay on the section model). Example: `bot.concurrent_updates` must fit the DB connection budget (`db.pool_size + db.max_overflow - POOL_CONNECTION_HEADROOM`) — a violation is a startup `ValidationError`, never a runtime surprise.

## Adding a new config field

1. Add the field to the appropriate Pydantic model in `config.py` (e.g., `BotConfig`, `DbConfig`). Use `SecretStr` for tokens, passwords, and API keys.
2. Give every field **without** a safe default a `Sample` annotation (`Annotated[T, Sample(value, comment=...)]` in `config.py`) — `mb setup` generates and refreshes `dev.toml` from these markers and fails loudly on a required field that lacks one (`tests/mb/test_setup.py` guards this). A defaulted field carries a `Sample` only when the generated dev value should differ from the model default (e.g. `engine_echo`).
3. Document the field with a `#` comment on the model — the config models in `config.py` are the full catalogue of options (linked from `mb setup`'s generated `dev.toml` and from `docs/contribute/setup.md`). Fields with a safe default may be omitted from the environment TOMLs — established practice for defaulted fields (`engine_echo`, the pool fields, `concurrent_updates`), and required when rollout happens via `MITUPBOT__` env-var overrides so revert stays config-only. Fields WITHOUT a safe default also go in `prod.toml`; developers pick them up locally by rerunning `uv run mb setup`, which adds missing required options to an existing `dev.toml` without touching values already set.
4. Document the corresponding environment variable override if applicable.
5. If adding an entirely new section, create a new Pydantic model and add it as a field on `Config`.

## Selecting the environment

The apps take an explicit `--env` option. Processes that cannot be handed one — the Alembic `env.py`, which Alembic itself invokes — call `env_from_environment()`, which reads the `MITUP_ENV` variable (`MITUP_ENV_VAR` in `config.py`) and defaults to `Env.DEV`, so local runs pick up `dev.toml` with nothing set. A value outside the `Env` enum raises rather than falling back to dev. The migrations Lambda sets `MITUP_ENV` to its own environment before invoking Alembic, so the deployed run reads `prod.toml` instead of a `dev.toml` that is not packaged in the image.

## Adding a new environment

The `Env` enum in `config.py` defines available environments. To add one:

1. Add a member to the `Env` enum.
2. Create the corresponding `libs/core/mitup_bot/environments/{env}.toml` file.
3. Populate all required config fields.

## Secrets

Sensitive values (`bot.token`, `db.password`, `google_api.*`) use Pydantic's `SecretStr`. Access the raw value via `.get_secret_value()`. Never log `SecretStr` fields directly — their `__str__` returns `"**********"`.

## Generating and refreshing `dev.toml`

`uv run mb setup --bot-token <token>` generates the git-ignored `environments/dev.toml` from the `Sample` annotations on the config models (rendered by `tools/mb/src/mb/setup_env.py`). A plain `uv run mb setup` refreshes an existing `dev.toml` in place: it adds any missing required option with its sample value and never touches values already set.

## Db-only config loading

Processes that need a database connection but not the full bot config — the Alembic `env.py` in `libs/data/mitup_bot/migrations/` and the migrations Lambda — build just the `[db]` section with `Config.db_from_providers(*providers)`. It merges providers exactly like `from_providers` but validates only `DbConfig`, and raises a `RuntimeError` pointing at `uv run mb setup` and the `MITUPBOT__DB__*` variables when the section is missing or incomplete. Never load the full `Config` just to reach `config.db`: the full model requires every section, which such processes don't have.
