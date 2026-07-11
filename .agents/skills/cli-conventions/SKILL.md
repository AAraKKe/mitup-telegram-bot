---
name: cli-conventions
description: CLI and recurrent-event conventions for mitup_bot. Auto-load when writing, editing, or reviewing the app CLI entry modules (bot_cli.py, events_cli.py, the rails-migration cli) or recurrent-event jobs under apps/events/mitup_bot/events/.
user-invocable: false
---

# CLI Conventions

Each deployable application owns a small CLI entry point built with [Click](https://click.palletsprojects.com/). There is no shared, auto-discovering CLI package — every app ships exactly one entry module and declares its own console script, because a workspace member may never be shipped by two distributions.

| App / tool | Entry module | Console script | Command(s) |
|---|---|---|---|
| `apps/bot` | `mitup_bot/bot_cli.py` | `mitup` | `launch` |
| `apps/events` | `mitup_bot/events_cli.py` | `mitup` | `recurrent-events` |
| `tools/rails-migration` | `mitup_bot/migration/cli.py` | `mitup-rails-migration` | (single command) |

## Scope

<critical_rules>
  <rule>These CLIs are for service entry points only — the commands the production image must run (launching the bot, running recurrent events) and the one-off rails data migration. Dev/CI tooling (deploying, managing locales, validating migrations) lives in the `mb` CLI, not here.</rule>
  <rule>Developer tooling belongs in `tools/` at the project root: repeatable workflows go in the `mb` CLI (`tools/mb/`), standalone one-off scripts next to it in `tools/`.</rule>
</critical_rules>

## Console scripts and the `mitup` command

The bot and events apps both expose a `mitup` console script (`mitup launch`, `mitup recurrent-events`). Those command strings are frozen — the ECS task definitions in the external mitup-infra repo invoke them as container commands. Each app declares its own `mitup` in `[project.scripts]`, exposing only its own subcommand; in production each image installs a single app, so there is no collision.

The dev workspace installs both apps into one shared venv, where a single `mitup` on PATH is ambiguous (last install wins). So `mb run bot` / `mb run events` invoke each app **by module** (`uv run python -m mitup_bot.bot_cli launch`), which is deterministic. Never rely on a bare `mitup` in the dev venv.

## Entry-module shape

The bot and events entry modules each define a Click group named `cli` (its subcommands are the frozen command names); the rails-migration entry module defines `cli` as a single `@click.command()` since it has only one command. All three expose a `main()` that calls `cli()`, wired as the console script (`mitup = "mitup_bot.bot_cli:main"`). The bot's `launch` instantiates `MitupRuntime` and calls `run()`; the events `recurrent-events` command parses intervals and delegates to `service.run_events`.

**Keep commands thin.** A command that only fronts a service must stay a thin entry point — parse options and delegate to the owning package, which holds the real logic and stays importable without dragging Click into a lambda. The `recurrent-events` command delegates to `mitup_bot.events` (jobs + `service.run_events`); the rails command delegates to `mitup_bot.migration` (the pipeline runner). Job implementations under `apps/events/mitup_bot/events/` never import a CLI entry module.

## Database and API access

CLI commands and the recurrent-event jobs both run outside the PTB application lifecycle, so they use the non-handler variants of the shared infrastructure. The details live in the owning skills — this section is a pointer, not a second copy of the rules:

- **Database:** sessions are injected by the async `@with_session` decorator from `mitup_bot.db`; sync Click entry points wrap the async pipeline in a single `asyncio.run(...)`. Jobs that broadcast over Telegram wrap each critical section — per meeting or per joined link, whichever row carries the job's flag — in `async with db.begin_write(api)`: the same capture → commit → drain → reconcile lifecycle as write-mode handlers, including the per-meeting row lock when the job mutates meetings. See the `database` skill for the full pattern.
- **Telegram API:** use `BotAdapter` wrapping an `ExtBot` — never `MitupContext`, which only exists inside the PTB app. See the `api-wrapper` skill for how to construct it and supply a `MetricsClient`.
- **Configuration:** load via `Config.from_providers()` with the appropriate `Env` — see the `mitup-config` skill.

## Helper utilities

The rails migration tool's console helpers live in `mitup_bot/migration/console.py`. Check for an existing helper before adding a new one.
