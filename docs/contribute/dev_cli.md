---
icon: material/console
---

# The mb CLI

`mb` is the developer CLI for this repository. It's a uv workspace member under [`tools/mb`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/tools/mb), and it fronts every routine task: tests, formatting, the local database, running the apps, translations, and the docs site. Run it through uv:

```bash
uv run mb --help
```

Every command groups its subcommands, and each group takes `--help`, so `uv run mb db --help` and `uv run mb locales --help` print exactly what's available. The sections below are a map of the groups you reach for most.

## Quality gates

The checks that decide whether your branch is ready. `uv run mb validate` runs the whole gate; the rest are the individual pieces.

| Command | What it does |
|---------|--------------|
| `mb test [paths]` | Run the suite, fast, no coverage. Extra args pass through to pytest. |
| `mb validate` | Format check, lint, type-check, and the full test suite with coverage, without stopping at the first failure. |
| `mb fix` | Format and apply every safe and unsafe lint fix. |
| `mb format` / `mb lint` / `mb typecheck` | The individual formatter, linter, and type checker. |

[Testing and validation](testing.md) covers these in depth, including the database suite and the per-language runs.

## Running things locally

Three groups drive the local stack: the database, the apps, and the docker compose services behind them.

`mb db` owns the local Postgres and the migration graph:

```bash
uv run mb db up             # start Postgres and wait until it's healthy
uv run mb db migrate up     # apply pending migrations
uv run mb db migrate down   # roll back one migration
uv run mb db migrate new "add reminder column"   # scaffold an empty revision
uv run mb db migrate validate                    # check the migration graph
```

`mb run` starts the applications against that database. Both take `--docker` to run inside docker compose instead of on your host:

```bash
uv run mb run bot           # start the bot
uv run mb run events        # start the recurrent-events worker
```

`mb docker` is the compose lifecycle itself, for when you want to manage the containers directly:

```bash
uv run mb docker up         # start compose services in the background
uv run mb docker ps         # list them
uv run mb docker logs       # tail their logs
uv run mb docker down       # stop and remove them
```

## Locales

Every user-facing string lives in code and is extracted into gettext catalogs under [`libs/core/mitup_bot/locales`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/core/mitup_bot/locales). `mb locales` manages them.

When you add or change a string in the message definitions, regenerate the English source catalog and rebuild:

```bash
uv run mb locales sync
```

`sync` is the one-shot workflow: it regenerates the English source catalog from the code, drops msgid blocks that no longer exist, recompiles every catalog, and validates that they all carry the same msgids. The individual steps are there when you need them:

| Command | What it does |
|---------|--------------|
| `mb locales update-source` | Regenerate the English source catalog from the message definitions in code. |
| `mb locales build` | Compile the `.po` sources into the `.mo` catalogs the bot loads. |
| `mb locales validate-ids` | Check that every string in code has an entry in the English source catalog. |
| `mb locales validate` | Check that every locale carries the same msgids as English. |
| `mb locales clean` | Remove stale msgid blocks from the non-English catalogs. |

The translations themselves are contributed on Crowdin, not edited by hand. See [translating Mitup](../collaborate/translator.md) for that side of the workflow.

## Docs

This site is built with Zensical. Preview it with live reload while you write, and build the static output before you push:

```bash
uv run mb docs serve        # live-reload preview
uv run mb docs build        # build the static site
```

A pre-commit hook runs `mb docs build` whenever you touch `docs/` or `zensical.toml`, so a broken build never reaches a merge request.

## Deploy and CI

Two groups you rarely run by hand. `mb deploy` pushes the four app images, updates both Lambdas, and rolls out the two ECS services in one command; the pipeline drives it. `mb ci` holds the checks the pipeline runs, such as the commit-message format check, the lock-file check, and the language-matrix check. You can run any of them locally to reproduce a CI failure, but the hooks and pipeline invoke them for you.
