# CLI

The bot includes a CLI built with [Click](https://click.palletsprojects.com/). The entry point is `mitup_bot/cli/run.py`, registered in `pyproject.toml` as the `mitup` console script. It instantiates `MitupCliCommand` (defined in `cli_commands.py`), a dynamic command group that auto-discovers subcommands.

## Scope

This CLI contains **production-related commands only** — commands that are part of the bot's operational lifecycle (launching, deploying, running migrations, managing translations, etc.). It is **not** a general-purpose tooling CLI.

Scripts for CI, development utilities, and one-off tooling belong in the `bin/` directory at the project root, not here.

## The `launch` command

The `launch` command (`mitup_bot/cli/commands/launch.py`) is the **entry point to start the bot**. It instantiates `MitupRuntime` with the given environment and calls `run()`:

```bash
hatch run dev:launch              # Launches with DEV environment (default)
hatch run dev:launch --env prod   # Launches with PROD environment
```

This is how the bot is started both locally and in production (ECS).

## Auto-discovery

`MitupCliCommand` scans `mitup_bot/cli/commands/` for Python files and registers each as a CLI subcommand. The mapping is:

- **Filename** `snake_case.py` → **Command** `kebab-case`
- Each file must define a Click command (typically via `@click.command()`)

To add a new CLI command, create a file in `mitup_bot/cli/commands/`. It will be automatically available — no registration step needed. Only add commands here if they serve a production or operational purpose.

## Top-level scripts vs. subcommands

| Location | Purpose | Example |
|----------|---------|---------|
| `mitup_bot/cli/commands/` | Production CLI subcommands (launch, deploy, translations) | `launch.py`, `deploy.py`, `translations.py` |
| `mitup_bot/cli/` (top-level) | Operational scripts invoked by lambdas or scheduled tasks | `inactive_meetings.py`, `user_cleanup.py`, `notify_meetings.py` |
| `bin/` (project root) | CI scripts, dev utilities, one-off tooling | `check_ty_ignores.py`, `check_commit_message.py`, `local-setup.sh` |

Top-level scripts in `mitup_bot/cli/` are not auto-discovered as CLI commands — they are imported and called directly (e.g., from a Lambda handler or a cron job).

## Database and API access in CLI code

CLI commands use the same infrastructure as the bot:

- **Database:** Use `@with_session` or `@with_async_session` from `mitup_bot.db` for session injection. The decorators work identically to handler usage.
- **Telegram API:** Use `BotAdapter` (not `MitupContext`) since CLI code runs outside the PTB application lifecycle. Construct it with an `ExtBot` instance.
- **Configuration:** Load via `Config.from_providers()` with the appropriate `Env`.

## Helper utilities

`mitup_bot/cli/helpers.py` contains shared utilities for CLI scripts (e.g., common Click options, output formatting). Check it before adding new helpers.

## Development commands

CLI commands registered under `commands/` are available via Hatch. See the `[tool.hatch.envs.dev.scripts]` section in `pyproject.toml` for the full list of available commands (e.g., `hatch run dev:launch`, `hatch run dev:deploy`).
