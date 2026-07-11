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

Reads `libs/core/mitup_bot/environments/{env}.toml` where `env` is a member of the `Env` enum (`DEV`, `PROD`, `SAMPLE`). TOML sections map to config groups.

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
2. Document the field in `sample.toml` (always, with its default noted). Fields with a safe default may be omitted from the other environment TOMLs — established practice for defaulted fields (`engine_echo`, the pool fields, `concurrent_updates`), and required when rollout happens via `MITUPBOT__` env-var overrides so revert stays config-only. Fields WITHOUT a safe default go in **all** TOMLs: `dev.toml`, `prod.toml`, `sample.toml`.
3. Document the corresponding environment variable override if applicable.
4. If adding an entirely new section, create a new Pydantic model and add it as a field on `Config`.

## Adding a new environment

The `Env` enum in `config.py` defines available environments. To add one:

1. Add a member to the `Env` enum.
2. Create the corresponding `libs/core/mitup_bot/environments/{env}.toml` file.
3. Populate all required config fields.

## Secrets

Sensitive values (`bot.token`, `db.password`, `google_api.*`) use Pydantic's `SecretStr`. Access the raw value via `.get_secret_value()`. Never log `SecretStr` fields directly — their `__str__` returns `"**********"`.

## The `sample.toml` file

`sample.toml` documents the full catalogue of config options (linked from `mb setup`'s generated `dev.toml` and from `docs/contribute/setup.md`) and is the TOML loaded for `Env.SAMPLE`, used by Alembic migrations (`libs/data/mitup_bot/migrations/env.py`) and by some tests. `uv run mb setup --bot-token <token>` generates `dev.toml` from its own built-in template (`tools/mb/src/mb/setup_env.py`), not by reading `sample.toml` — keep both the built-in template and `sample.toml` in sync whenever fields are added or removed, since neither is generated from the other.

When using the `sample.toml` as a template, make sure to copy it before editing to a `dev.toml` that is not checked into version control. The `sample.toml` should never contain real secrets or environment-specific values.
