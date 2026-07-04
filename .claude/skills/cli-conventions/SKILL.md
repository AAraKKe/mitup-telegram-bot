---
name: cli-conventions
description: CLI conventions for mitup_bot. Auto-load when writing, editing, or reviewing CLI commands in mitup_bot/cli/.
user-invocable: false
---

# CLI Conventions

The bot includes a CLI built with [Click](https://click.palletsprojects.com/). The entry point is `mitup_bot/cli/run.py`, registered in `pyproject.toml` as the `mitup` console script.

## Scope

<critical_rules>
  <rule>This CLI is for production-related commands only — commands that are part of the bot's operational lifecycle (launching, deploying, running migrations, managing translations, etc.). It is NOT a general-purpose tooling CLI.</rule>
  <rule>Scripts for CI, development utilities, and one-off tooling belong in the `bin/` directory at the project root, not in `mitup_bot/cli/`.</rule>
</critical_rules>

## The `launch` command

The `launch` command (`mitup_bot/cli/commands/launch.py`) is the entry point to start the bot. It instantiates `MitupRuntime` with the given environment and calls `run()`.

## Auto-discovery

`MitupCliCommand` scans `mitup_bot/cli/commands/` for Python files and registers each as a CLI subcommand:

- **Filename** `snake_case.py` → **Command** `kebab-case`
- Each file must define a Click command (typically via `@click.command()`)

To add a new CLI command, create a file in `mitup_bot/cli/commands/`. It will be automatically available — no registration step needed.

## Three-tier location rule

| Location | Purpose | Example |
|----------|---------|---------|
| `mitup_bot/cli/commands/` | Production CLI subcommands | `launch.py`, `deploy.py`, `translations.py` |
| `mitup_bot/cli/` (top-level) | Operational scripts invoked by lambdas or scheduled tasks | `inactive_meetings.py`, `user_cleanup.py` |
| `bin/` (project root) | CI scripts, dev utilities, one-off tooling | `check_ty_ignores.py`, `local-setup.sh` |

Top-level scripts in `mitup_bot/cli/` are not auto-discovered — they are imported and called directly (e.g., from a Lambda handler or a cron job).

## Database and API access

CLI commands run outside the PTB application lifecycle, so they use the non-handler variants of the shared infrastructure. The details live in the owning skills — this section is a pointer, not a second copy of the rules:

- **Database:** sessions are injected by the async `@with_session` decorator from `mitup_bot.db`; sync Click entry points wrap the async pipeline in a single `asyncio.run(...)`. Jobs that broadcast over Telegram wrap each critical section — per meeting or per joined link, whichever row carries the job's flag — in `async with db.begin_write(api)`: the same capture → commit → drain → reconcile lifecycle as write-mode handlers, including the per-meeting row lock when the job mutates meetings. See the `database` skill for the full pattern.
- **Telegram API:** use `BotAdapter` wrapping an `ExtBot` — never `MitupContext`, which only exists inside the PTB app. See the `api-wrapper` skill for how to construct it and supply a `MetricsClient`.
- **Configuration:** load via `Config.from_providers()` with the appropriate `Env` — see the `mitup-config` skill.

## Helper utilities

`mitup_bot/cli/helpers.py` contains shared utilities for CLI scripts. Check it before adding new helpers.
