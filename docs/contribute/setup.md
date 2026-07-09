---
icon: material/wrench-outline
---

# Setup

## Contributor requirements

Mitup is built in Python, deployed in containers on AWS and managed using [Hatch](https://hatch.pypa.io) environments. Contributors must have:

*   A Docker installation ([Docker Desktop](https://www.docker.com/products/docker-desktop/) works well if you don't have one)
*   Knowledge of modern Python, including type annotations
*   A GitLab account

## Tools

### Hatch

If you do not have a working installation of Hatch, install it now. We recommend installing Hatch through [pipx](https://pipx.pypa.io/stable/installation/).

```bash
pipx install hatch
```

Since we run all commands as part of the Hatch `dev` environment, there's no need to install a specific Python distribution. Hatch manages this automatically when running any command. Mitup is configured to use a specific version of Python, and Hatch attempts to locate a compatible version on your system. If none is found, Hatch installs the required version for the virtual environment `dev`.

### Pre-commit

We use [pre-commit](https://pre-commit.com/) to handle validations for each commit to the repository. Make sure to install it before committing any code to be pushed to the GitLab repo.

```bash
pipx install pre-commit
```

### Gettext

While most of the dependencies are handled by Hatch, [gettext](https://www.gnu.org/software/gettext/) needs to be installed on your system before you can run Mitup. It handles the translation files. Follow the instructions on their site to install it.

!!! note "No PostgreSQL client libraries needed"

    Mitup talks to PostgreSQL through [psycopg](https://www.psycopg.org/psycopg3/docs/), pinned with the `binary` extra in [`pyproject.toml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/pyproject.toml). The binary wheels bundle `libpq`, so you don't need a C compiler or a separate PostgreSQL installation.

### Set up local repository

Start by cloning the Mitup code from our [public repo](https://gitlab.com/meetupbot/mitup-telegram-bot)

```bash
git clone git@gitlab.com:meetupbot/mitup-telegram-bot.git
cd mitup-telegram-bot
```

Set up `pre-commit`:

```bash
pre-commit install
pre-commit run --all-files
```

Now let's run the validations. This command will trigger the creation of the `dev` Hatch environment, which is used for all development-related activities.

```bash
hatch run dev:validate
```

!!! info "VS Code and forks"
    A setup script is included in the repo. It writes a standard `.vscode/settings.json` with the configuration needed to run type checking and formatting correctly, which VS Code and forks like Cursor read the same way. Run:
    ```
    hatch run dev:python bin/setup_vscode.py
    ```

    !!! tip "Type hints in your editor"
        For a better IDE experience with type hints, point your editor at the `dev` environment's interpreter (`hatch env find dev` prints its location). It includes boto3-stubs for enhanced autocompletion and type checking.

### Configure your development bot

To test your changes through Telegram, you need to link a bot to Mitup. Head to [BotFather](https://t.me/BotFather) and register a new bot:

*   Run the `/newbot` command
*   Choose a name for the bot, e.g. `Mitup-<yourname>-dev`
*   Choose a username for the bot, e.g. `mitup_yourname_dev_bot`
*   Copy the token provided by BotFather

Next, run the following command:

```bash
hatch run dev:set-local-bot <token>
```

This creates a `dev.toml` configuration file used by Mitup when running the bot locally via Docker. If `dev.toml` already exists, the command asks before overwriting it; pass `--force` to skip the prompt. To try broadcast features, add one or more `--admin-id <telegram-user-id>` options to pre-fill `admin_tg_ids` in the generated file.

### Set up your database

For local execution, we rely on a Docker instance of PostgreSQL. First, run all database migrations to ensure the database schema is correctly set up:

```bash
docker compose run migrations-upgrade
```

This command spins up a PostgreSQL database with a local volume in `./postgres-data` that persists data between executions, and then runs the necessary migrations.

## Launch Mitup

Once setup is complete, start the bot:

```bash
docker compose run mitup
```

Open the bot in Telegram via BotFather.
