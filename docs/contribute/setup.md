---
icon: material/wrench-outline
---

# Setup

## Contributor requirements

Mitup is a Python project, managed with [uv](https://docs.astral.sh/uv/) and deployed as containers on AWS. To work on it you need:

* [uv](https://docs.astral.sh/uv/getting-started/installation/), which manages the Python version, the virtual environment, and every dependency
* A Docker installation ([Docker Desktop](https://www.docker.com/products/docker-desktop/) works well if you don't have one), used for local Postgres and for running the bot in a container
* [gettext](https://www.gnu.org/software/gettext/), which compiles the translation catalogs
* A GitLab account
* Working knowledge of modern Python, including type annotations

uv installs the right Python version for you the first time you sync, so you don't need to install a specific distribution yourself. gettext is the one system dependency uv can't provide; install it with your package manager (`brew install gettext` on macOS, `apt install gettext` on Debian/Ubuntu). You don't need any PostgreSQL client libraries.

!!! note "No PostgreSQL client libraries needed"

    Mitup talks to PostgreSQL through [psycopg](https://www.psycopg.org/psycopg3/docs/), pinned with the `binary` extra in [`pyproject.toml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/pyproject.toml). The binary wheels bundle `libpq`, so you don't need a C compiler or a separate PostgreSQL installation.

## Clone and bootstrap

Clone the repository from the [public repo](https://gitlab.com/meetupbot/mitup-telegram-bot):

```bash
git clone git@gitlab.com:meetupbot/mitup-telegram-bot.git
cd mitup-telegram-bot
```

Create the environment and install the git hooks in one step:

```bash
uv sync
uv run mb setup
```

`uv sync` reads [`uv.lock`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/uv.lock) and builds a `.venv` at the repo root with every workspace member and dependency. `uv run mb setup` bootstraps the rest of the checkout: it runs `uv sync` again (so it's safe to run on its own), installs the [pre-commit](https://pre-commit.com/) hooks, and copies any local-only config from your main checkout when you're in a worktree. The command is idempotent, so you can re-run it any time your setup drifts.

`mb` is the developer CLI for this repository. Every task below runs through it. See [the mb CLI](dev_cli.md) for the full command surface, or run `uv run mb --help`.

!!! tip "Type hints in your editor"

    Point your editor's interpreter at the workspace virtual environment (`.venv/bin/python` at the repo root). It carries boto3-stubs and the project's type checker, so autocompletion and inline type checking match what CI runs. To generate a shared VS Code (or Cursor) config with the right interpreter and formatter wired up, run `uv run mb setup --vscode`.

## Configure your development bot

To drive your changes through Telegram, link a bot to Mitup. Open [BotFather](https://t.me/BotFather) and register one:

* Run the `/newbot` command
* Choose a name, e.g. `Mitup-<yourname>-dev`
* Choose a username, e.g. `mitup_yourname_dev_bot`
* Copy the token BotFather gives you

Write that token into your local development config:

```bash
uv run mb setup --bot-token <token>
```

This generates a `dev.toml` config file that Mitup reads when running locally. If it already exists, the command asks before overwriting; pass `--force` to skip the prompt. Rerun `uv run mb setup` without `--bot-token` whenever the project gains required config options: it adds any option your `dev.toml` is missing, with a sample value, and leaves every value you already set untouched. To try the broadcast features, add one or more `--admin-id <telegram-user-id>` options and the ids land in `bot.admin_tg_ids`. The full catalogue of config options lives on the config models in [`config.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/core/mitup_bot/config.py).

## Set up your database

Local runs use a Postgres container. Start it and apply the schema:

```bash
uv run mb db up
uv run mb db migrate up
```

`mb db up` starts the Postgres container and waits until it reports healthy. `mb db migrate up` runs every pending migration. The container keeps its data in a local `./postgres-data` volume, so the schema and rows survive restarts.

## Launch Mitup

Start the bot:

```bash
uv run mb run bot
```

That runs the bot on your host against the local Postgres. To run it inside docker compose instead, add `--docker`. The recurrent-events worker runs the same way with `uv run mb run events`. Open your bot in Telegram and start a conversation.

Once the bot is running, [the mb CLI](dev_cli.md) covers the rest of the day-to-day workflow: tests, migrations, locales, and the local container lifecycle.
